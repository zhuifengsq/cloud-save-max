from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user, require_permissions
from app.core.permissions import TASK_WRITE
from app.db.session import get_db
from app.models.task import Task
from app.models.task_execution import TaskExecution
from app.schemas.dashboard import CapacityOverviewOut, DramaOverviewOut
from app.services import audit
from app.services.dashboard_drama import build_drama_overview
from app.services.drive_accounts import build_capacity_overview

router = APIRouter()


@router.get('/capacity-overview', response_model=CapacityOverviewOut)
def get_capacity_overview(_current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    return CapacityOverviewOut(**build_capacity_overview(db))


@router.get('/drama-overview', response_model=DramaOverviewOut)
def get_drama_overview(
    days: int = Query(default=30, ge=1, le=365),
    _current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return DramaOverviewOut(**build_drama_overview(db, days=days))

@router.post('/drama/clear-failures', dependencies=[Depends(require_permissions(TASK_WRITE))])
def clear_drama_failures(
    request: Request,
    days: int = Query(default=0, ge=0, le=365),
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """删除追剧任务的失败执行记录；days>0 时只删该天数之前的。"""
    task_ids = db.query(Task.id).filter(Task.task_type == 'drama')
    query = db.query(TaskExecution).filter(
        TaskExecution.status == 'failed',
        TaskExecution.task_id.in_(task_ids),
    )
    if days > 0:
        query = query.filter(TaskExecution.started_at < datetime.utcnow() - timedelta(days=days))
    deleted = int(query.delete(synchronize_session=False) or 0)
    audit.write_audit_log(
        db,
        actor_user_id=current.user.id,
        action='task.execution.clear_failures',
        target_type='task_execution',
        target_id='failed',
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get('user-agent'),
        success=True,
        detail=f'days={days} deleted={deleted}',
    )
    db.commit()
    return {'deleted': deleted}

