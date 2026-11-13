"""Bounded task reads; legacy collection route remains unchanged."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.api.deps import require_permissions
from app.core.permissions import TASK_READ
from app.core.errors import bad_request, not_found
from app.application.container import get_db
from app.application.queries.task_reads import (
    page_tasks,
    order_index,
    overview_tasks,
    detail_task,
)
from app.schemas.task_reads import (
    TaskPageOut,
    TaskOrderIndexOut,
    TaskOverviewOut,
    TaskSummaryOut,
)

router = APIRouter(dependencies=[Depends(require_permissions(TASK_READ))])


def filters(
    q: str = "",
    task_type: str | None = None,
    enabled: bool | None = None,
    drive_type: str | None = None,
    media_category: str | None = None,
    failed_only: bool = False,
):
    return dict(
        q=q,
        task_type=task_type,
        enabled=enabled,
        drive_type=drive_type,
        media_category=media_category,
        failed_only=failed_only,
    )


@router.get("/page", response_model=TaskPageOut)
def page(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    ids: str | None = None,
    conditions: dict = Depends(filters),
    db: Session = Depends(get_db),
):
    try:
        selected = None if ids is None else [int(value) for value in ids.split(",")]
        return page_tasks(
            db, page=page, page_size=page_size, ids=selected, **conditions
        )
    except ValueError as exc:
        raise bad_request("invalid_task_ids", "任务 ID 无效或不符合筛选条件") from exc


@router.get("/order-index", response_model=TaskOrderIndexOut)
def index(conditions: dict = Depends(filters), db: Session = Depends(get_db)):
    return order_index(db, **conditions)


@router.get("/overview", response_model=list[TaskOverviewOut])
def overview(db: Session = Depends(get_db)):
    return overview_tasks(db)


@router.get("/{task_id:int}", response_model=TaskSummaryOut)
def detail(task_id: int, db: Session = Depends(get_db)):
    item = detail_task(db, task_id)
    if item is None:
        raise not_found("task_not_found", "任务不存在")
    return item
