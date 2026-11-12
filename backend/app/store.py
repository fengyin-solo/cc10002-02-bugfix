"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。
"""
from __future__ import annotations

from typing import Any

from app.seed import SEED_ROWS


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        # 操作台账：module -> entry_id -> 按时间追加的动作记录（接受、幂等、拒绝都留痕）。
        self._ledgers: dict[str, dict[int, list[dict[str, Any]]]] = {}
        # 台账流水号：module -> entry_id -> 自增序号。
        self._ledger_seq: dict[str, dict[int, int]] = {}

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def ledger(self, module: str, entry_id: int) -> list[dict[str, Any]]:
        """读取一条记录的操作台账；返回副本，避免调用方直接篡改留痕。"""
        bucket = self._ledgers.setdefault(module, {}).setdefault(entry_id, [])
        return [dict(record) for record in bucket]

    def next_ledger_id(self, module: str, entry_id: int) -> int:
        seq = self._ledger_seq.setdefault(module, {})
        seq[entry_id] = seq.get(entry_id, 0) + 1
        return seq[entry_id]

    def append_ledger(self, module: str, entry_id: int, record: dict[str, Any]) -> None:
        bucket = self._ledgers.setdefault(module, {}).setdefault(entry_id, [])
        bucket.append(dict(record))

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
