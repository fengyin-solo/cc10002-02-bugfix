"""动作执行链共用的工作流引擎。

集中解决三条执行链上反复出现的失真问题：

1. 动作协议：历史客户端存在两种请求体——页面发送的顶层 ``{"action": "启动实验"}``
   与嵌套的 ``{"values": {"action": "启动实验"}}``。两种都要支持；若同时出现且取值
   不一致，属于冲突输入，必须拒绝，不允许随机挑一个执行。
2. 状态机：状态只能沿既定顺序单步推进，不允许倒序、跳级，也不允许从终态再次流转。
   对同一动作的重复提交按幂等处理（不重复改写、返回当前状态并提示）。
3. 留痕与并发：每次尝试（接受 / 幂等 / 拒绝）都写入操作台账，拒绝必须带原因；
   状态变更在进程级锁内完成，完成与终止并发竞争时只有一条结论能被接受。
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from app.store import store

# 全进程共用一把锁：内存仓库没有数据库事务，状态推进必须串行化，
# 才能保证“完成与终止竞争时只接受一条合法结论”。
_WORKFLOW_LOCK = threading.RLock()


def resolve_action(top_level: Any, nested_values: dict[str, Any] | None) -> tuple[str | None, str | None]:
    """解析两种动作请求，返回 ``(动作, 错误原因)``。

    - 仅一种请求体携带动作：正常返回该动作。
    - 两种都携带且取值一致：视为同一动作，正常返回。
    - 两种都携带但取值不一致：冲突，返回错误原因，调用方必须拒绝。
    - 都没有携带：返回空动作与提示原因。
    """
    top = str(top_level).strip() if top_level is not None else ""
    nested_raw = (nested_values or {}).get("action")
    nested = str(nested_raw).strip() if nested_raw is not None else ""

    if top and nested:
        if top != nested:
            return None, (
                f"动作输入冲突：顶层 action=「{top}」与 values.action=「{nested}」"
                "不一致，请统一后重试，系统不会随机选择动作"
            )
        return top, None
    action = top or nested
    if not action:
        return None, "请求缺少动作：请在顶层 action 或 values.action 中提供要执行的动作"
    return action, None


def migrate_legacy_rows(
    module: str,
    *,
    status_order: list[str],
    terminal_statuses: set[str],
    negative_statuses: set[str],
) -> None:
    """历史数据迁移：只校正派生标记，不改动真实状态、不补造动作台账。

    - 运行中的历史样例保持「运行中」，不会被当成重新启动的草稿。
    - ``pending`` 以“是否处于终态”为准重新计算，修正旧逻辑里终态仍挂起的问题。
    - ``abnormal`` 以负向终态（已终止 / 已失败）为准重新计算。
    - 无法识别的状态保留原值并视为未决，避免静默篡改历史数据。

    该函数幂等：重复执行不会产生新的变化，也不会写入任何台账记录。
    """
    for row in store.rows(module):
        status = str(row.get("status") or "")
        if status in status_order:
            row["pending"] = status not in terminal_statuses
            row["abnormal"] = status in negative_statuses


class WorkflowEngine:
    """按显式前驱表驱动的顺序状态机，并负责动作留痕。"""

    def __init__(
        self,
        module: str,
        entity_label: str,
        *,
        status_order: list[str],
        # 动作 -> (要求的当前状态, 目标状态)；只允许从前驱单步推进到目标。
        transitions: dict[str, tuple[str, str]],
        terminal_statuses: set[str],
        negative_statuses: set[str],
    ) -> None:
        self.module = module
        self.entity_label = entity_label
        self.status_order = status_order
        self.transitions = transitions
        self.terminal_statuses = terminal_statuses
        self.negative_statuses = negative_statuses
        # 启动即做一次幂等迁移，保证历史样例的派生标记与新口径一致。
        migrate_legacy_rows(
            module,
            status_order=status_order,
            terminal_statuses=terminal_statuses,
            negative_statuses=negative_statuses,
        )

    def ledger(self, entry_id: int) -> list[dict[str, Any]]:
        return store.ledger(self.module, entry_id)

    def apply(
        self,
        entry_id: int,
        action: str,
        *,
        source: str = "unknown",
    ) -> tuple[dict[str, Any] | None, str, bool]:
        """执行一次动作，返回 ``(记录, 消息, 是否幂等未变更)``。

        被拒绝时记录为 ``None``，消息即拒绝原因；接受与幂等都会返回当前记录。
        全程持锁，保证并发竞争下状态判断与写入是原子的。
        """
        with _WORKFLOW_LOCK:
            entry = store.find(self.module, entry_id)
            if entry is None:
                return None, f"{self.entity_label} {entry_id} 不存在或已归档", False

            current = str(entry.get("status") or "")

            if action not in self.transitions:
                reason = f"动作「{action}」不属于{self.entity_label}可执行范围"
                self._log(entry_id, action, current, current, accepted=False, idempotent=False,
                          reason=reason, source=source)
                return None, reason, False

            required, target = self.transitions[action]

            # 幂等：动作的目标状态就是当前状态，重复提交不重复改写、不产生副作用。
            if current == target:
                message = f"{self.entity_label}已处于「{target}」，「{action}」无需重复执行（幂等）"
                self._log(entry_id, action, current, target, accepted=False, idempotent=True,
                          reason=message, source=source)
                return entry, message, True

            # 顺序约束：只允许从规定的前驱状态单步推进。
            if current != required:
                if current in self.terminal_statuses:
                    reason = (
                        f"{self.entity_label}已处于终态「{current}」，终态不可再流转，"
                        f"动作「{action}」被拒绝"
                    )
                else:
                    reason = (
                        f"当前状态「{current}」不允许执行「{action}」：该动作要求先处于「{required}」，"
                        f"禁止跳级或回退（状态顺序：{' → '.join(self.status_order)}）"
                    )
                self._log(entry_id, action, current, current, accepted=False, idempotent=False,
                          reason=reason, source=source)
                return None, reason, False

            # 合法推进：在锁内完成状态与派生标记的写入。
            entry["status"] = target
            entry["pending"] = target not in self.terminal_statuses
            entry["abnormal"] = target in self.negative_statuses
            message = f"{self.entity_label}已{action}，状态由「{current}」推进至「{target}」"
            self._log(entry_id, action, current, target, accepted=True, idempotent=False,
                      reason=message, source=source)
            return entry, message, False

    def _log(
        self,
        entry_id: int,
        action: str,
        from_status: str,
        to_status: str,
        *,
        accepted: bool,
        idempotent: bool,
        reason: str,
        source: str,
    ) -> None:
        record = {
            "id": store.next_ledger_id(self.module, entry_id),
            "time": datetime.now().isoformat(timespec="seconds"),
            "entry_id": entry_id,
            "action": action,
            "from_status": from_status,
            "to_status": to_status,
            "accepted": accepted,
            "idempotent": idempotent,
            "reason": reason,
            "source": source,
        }
        store.append_ledger(self.module, entry_id, record)
