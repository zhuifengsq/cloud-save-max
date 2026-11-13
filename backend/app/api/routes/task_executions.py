"""On-demand execution history and bounded UTF-8 log chunks."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.api.deps import require_permissions
from app.core.permissions import TASK_READ
from app.core.errors import not_found
from app.application.container import get_db
from app.application.queries.task_executions import (
    execution_page,
    execution_detail,
    task_exists,
)
from app.schemas.task_reads import TaskExecutionPageOut, TaskExecutionDetailOut

router = APIRouter(dependencies=[Depends(require_permissions(TASK_READ))])


@router.get("/{task_id:int}/executions", response_model=TaskExecutionPageOut)
def page(
    task_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    if not task_exists(db, task_id):
        raise not_found("execution_not_found", "执行记录不存在")
    return execution_page(db, task_id, page=page, page_size=page_size)


@router.get(
    "/{task_id:int}/executions/{execution_id:int}",
    response_model=TaskExecutionDetailOut,
)
def detail(
    task_id: int,
    execution_id: int,
    log_offset: int = Query(0, ge=0),
    log_limit: int = Query(65536, ge=4, le=65536),
    db: Session = Depends(get_db),
):
    item = execution_detail(
        db, task_id, execution_id, log_offset=log_offset, log_limit=log_limit
    )
    if item is None:
        raise not_found("execution_not_found", "执行记录不存在")
    return item
