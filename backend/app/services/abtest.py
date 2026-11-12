"""AB实验业务规则：状态流转、字段校验、动作留痕与筛选口径都收在这里。

状态顺序（只允许单步向前，终态不可再流转）：
    草稿 → 待审批 → 运行中 → 已完成（正常归档，终态）
                                  ↘ 已终止（停止实验，终态）
"""
from __future__ import annotations

from typing import Any

from app.store import store
from app.workflow import WorkflowEngine

MODULE = "abtest"
ENTITY_LABEL = "实验方案"
REQUIRED_FIELDS = ["方案编号", "实验名称", "对照组配置"]
STATUS_ORDER = ["草稿", "待审批", "运行中", "已完成", "已终止"]
# 动作 -> (要求的当前状态, 目标状态)：每个动作都只能从唯一前驱单步推进。
TRANSITIONS = {
    "提交审批": ("草稿", "待审批"),
    "启动实验": ("待审批", "运行中"),
    "停止实验": ("运行中", "已终止"),
    "归档实验": ("运行中", "已完成"),
}
TERMINAL_STATUSES = {"已完成", "已终止"}
NEGATIVE_STATUSES = {"已终止"}

engine = WorkflowEngine(
    MODULE,
    ENTITY_LABEL,
    status_order=STATUS_ORDER,
    transitions=TRANSITIONS,
    terminal_statuses=TERMINAL_STATUSES,
    negative_statuses=NEGATIVE_STATUSES,
)


class AbtestService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("方案编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def get_ledger(self, entry_id: int) -> list[dict[str, Any]] | None:
        if store.find(MODULE, entry_id) is None:
            return None
        return engine.ledger(entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(
        self,
        entry_id: int,
        action: str,
        *,
        source: str = "unknown",
    ) -> tuple[dict[str, Any] | None, str, bool]:
        return engine.apply(entry_id, action, source=source)
