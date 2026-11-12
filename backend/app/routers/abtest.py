"""AB实验接口：维护实验方案，覆盖提交审批、启动实验、停止实验、归档实验等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionPayload, ActionResult, EntryPayload, PageResult
from app.services.abtest import STATUS_ORDER, AbtestService
from app.workflow import resolve_action

router = APIRouter(prefix="/api/abtest", tags=["AB实验"])

service = AbtestService()

LIST_FIELDS = ["方案编号", "实验名称", "对照组配置", "实验组配置", "流量分配比例", "核心指标", "最小样本量", "置信水平"]
STATUSES = STATUS_ORDER


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按方案编号检索"),
    status: str | None = Query(default=None, description="草稿、待审批、运行中、已完成、已终止"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按方案编号与状态过滤AB实验列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出AB实验清单：返回当前过滤条件下的全量数据。

    必须声明在 ``/{entry_id}`` 之前，否则 ``export`` 会被当成 entry_id 匹配而报 422。
    """
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "abtest", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条实验方案明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"实验方案 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条实验方案，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="实验方案已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: ActionPayload) -> ActionResult:
    """对单条实验方案执行提交审批、启动实验、停止实验、归档实验。

    同时兼容顶层 ``action`` 与嵌套 ``values.action`` 两种请求；两者冲突或缺动作时
    返回业务拒绝（HTTP 200、ok=False 并带原因），不做随机选择。状态机拒绝的跳级、
    回退、终态再流转同样以 ok=False 返回原因，并在操作台账中留痕。
    """
    action, conflict = resolve_action(payload.action, payload.values)
    if conflict is not None:
        return ActionResult(ok=False, message=conflict)
    entry, message, idempotent = service.run_action(entry_id, action or "", source="http")
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry, idempotent=idempotent)


@router.get("/{entry_id}/ledger")
def get_ledger(entry_id: int) -> dict[str, Any]:
    """返回实验方案的操作台账：实际接受的动作、幂等重试与拒绝原因都按时间排列。"""
    ledger = service.get_ledger(entry_id)
    if ledger is None:
        raise HTTPException(status_code=404, detail=f"实验方案 {entry_id} 不存在或已归档")
    return {"module": "abtest", "entry_id": entry_id, "total": len(ledger), "items": ledger}
