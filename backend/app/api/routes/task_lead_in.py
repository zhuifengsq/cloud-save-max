"""任务引流文件端点：预览、确认写入与最近一次记录（挂载在 /tasks 前缀下）。

引流是独立的手动操作，不进入任务执行/转存链路。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user, require_permissions
from app.core.permissions import TASK_READ, TASK_WRITE
from app.application.container import get_db, task_lead_in_use_case
from app.schemas.drama_lead_in import (
    DramaLeadInConfirmIn,
    DramaLeadInConfirmOut,
    DramaLeadInPreviewOut,
    DramaLeadInRecordOut,
)
from app.application.use_cases.task_lead_in import LeadInCommand
from app.application.lead_in.records import get_task_lead_in_state
from app.application.tasks import get_task

router = APIRouter()


@router.get(
    "/{task_id:int}/lead-in/preview",
    response_model=DramaLeadInPreviewOut,
    dependencies=[Depends(require_permissions(TASK_WRITE))],
)
def preview_task_lead_in(
    task_id: int, db: Session = Depends(get_db)
) -> DramaLeadInPreviewOut:
    result = task_lead_in_use_case(db).preview(LeadInCommand(task_id=task_id))
    return DramaLeadInPreviewOut(**result.data)


@router.post(
    "/{task_id:int}/lead-in/confirm",
    response_model=DramaLeadInConfirmOut,
    dependencies=[Depends(require_permissions(TASK_WRITE))],
)
def post_task_lead_in_confirm(
    task_id: int,
    payload: DramaLeadInConfirmIn,
    request: Request,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DramaLeadInConfirmOut:
    result = task_lead_in_use_case(db).confirm(
        LeadInCommand(
            task_id=task_id,
            preview_token=payload.preview_token,
            actor_user_id=current.user.id,
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
    )
    return DramaLeadInConfirmOut(**result.data)


@router.get(
    "/{task_id:int}/lead-in",
    response_model=DramaLeadInRecordOut,
    dependencies=[Depends(require_permissions(TASK_READ))],
)
def get_task_lead_in(
    task_id: int, db: Session = Depends(get_db)
) -> DramaLeadInRecordOut:
    task = get_task(db, task_id)
    return DramaLeadInRecordOut(**get_task_lead_in_state(db, task))
