from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_permissions
from app.core.permissions import AUDIT_READ, AUDIT_WRITE
from app.application.container import get_db, transaction
from app.application.container import audit_repo
from app.schemas.audit_log import AuditLogListOut
from app.api.presenters.audit_log import audit_log_list
from app.application.queries.audit_logs import list_audit_log_views


router = APIRouter()


class AuditLogSchedulerIn(BaseModel):
    enabled: bool | None = None
    crontab: str | None = None
    timezone: str | None = None
    retention_days: int | None = None


class AuditLogSchedulerOut(BaseModel):
    enabled: bool
    crontab: str
    timezone: str
    retention_days: int


def _get_or_create_scheduler(db: Session):
    """取审计调度设置；不存在时在事务内建默认行（与 S3 前的行为一致）。"""
    with transaction(db):
        setting = audit_repo().get_or_create_scheduler_setting(db)
    db.refresh(setting)
    return setting


@router.get("", response_model=AuditLogListOut, dependencies=[Depends(require_permissions(AUDIT_READ))])
def get_audit_logs(
    page: int = 1,
    page_size: int = 20,
    q: str | None = None,
    action: str | None = None,
    success: bool | None = None,
    db: Session = Depends(get_db),
):
    # 查询口径收在 application/queries，路由只做视图 → 响应模型（presenter）。
    views, total = list_audit_log_views(db, page=page, page_size=page_size, q=q, action=action, success=success)
    return audit_log_list(views, page=page, page_size=page_size, total=total)


@router.delete("", dependencies=[Depends(require_permissions(AUDIT_WRITE))])
def delete_all_audit_logs(db: Session = Depends(get_db)):
    with transaction(db):
        audit_repo().delete_all(db)
    return {"ok": True}


@router.get("/scheduler", response_model=AuditLogSchedulerOut, dependencies=[Depends(require_permissions(AUDIT_READ))])
def get_audit_log_scheduler(db: Session = Depends(get_db)):
    setting = _get_or_create_scheduler(db)
    return AuditLogSchedulerOut(
        enabled=setting.enabled,
        crontab=setting.crontab,
        timezone=setting.timezone,
        retention_days=setting.retention_days,
    )


@router.patch("/scheduler", response_model=AuditLogSchedulerOut, dependencies=[Depends(require_permissions(AUDIT_WRITE))])
def update_audit_log_scheduler(payload: AuditLogSchedulerIn, db: Session = Depends(get_db)):
    setting = _get_or_create_scheduler(db)
    with transaction(db):
        if payload.enabled is not None:
            setting.enabled = payload.enabled
        if payload.crontab is not None:
            setting.crontab = payload.crontab
        if payload.timezone is not None:
            setting.timezone = payload.timezone
        if payload.retention_days is not None:
            setting.retention_days = payload.retention_days
    db.refresh(setting)
    return AuditLogSchedulerOut(
        enabled=setting.enabled,
        crontab=setting.crontab,
        timezone=setting.timezone,
        retention_days=setting.retention_days,
    )

