"""ETL任务业务规则：状态流转、字段校验、动作留痕与筛选口径都收在这里。

状态顺序（只允许单步向前，终态不可再流转）：
    草稿 → 待调度 → 运行中 → 已完成（归档结果，终态）
                                  ↘ 已失败（终止任务，终态）
"""
from __future__ import annotations

from typing import Any

from app.store import store
from app.workflow import WorkflowEngine

MODULE = "etljob"
ENTITY_LABEL = "加工任务"
REQUIRED_FIELDS = ["任务编号", "输入源", "输出目标"]
STATUS_ORDER = ["草稿", "待调度", "运行中", "已完成", "已失败"]
# 动作 -> (要求的当前状态, 目标状态)：每个动作都只能从唯一前驱单步推进。
TRANSITIONS = {
    "提交调度": ("草稿", "待调度"),
    "启动任务": ("待调度", "运行中"),
    "终止任务": ("运行中", "已失败"),
    "归档结果": ("运行中", "已完成"),
}
TERMINAL_STATUSES = {"已完成", "已失败"}
NEGATIVE_STATUSES = {"已失败"}

engine = WorkflowEngine(
    MODULE,
    ENTITY_LABEL,
    status_order=STATUS_ORDER,
    transitions=TRANSITIONS,
    terminal_statuses=TERMINAL_STATUSES,
    negative_statuses=NEGATIVE_STATUSES,
)


class EtljobService:
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
            rows = [row for row in rows if keyword in str(row.get("任务编号", ""))]
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
