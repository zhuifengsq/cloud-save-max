"""审计日志与审计调度设置的仓储（唯一碰 AuditLog/AuditLogSchedulerSetting 的地方之一）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Select, delete, func, or_, select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.audit_log_scheduler_setting import AuditLogSchedulerSetting
from app.models.user import User


def add_audit_log(
    db: Session,
    *,
    actor_user_id: int | None,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    success: bool = True,
    detail: str | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_user_id=actor_user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            ip=ip,
            user_agent=user_agent,
            success=success,
            detail=detail,
        )
    )


def list_audit_logs(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 20,
    q: str | None = None,
    action: str | None = None,
    success: bool | None = None,
) -> tuple[list[Any], int]:
    page = max(1, int(page))
    page_size = min(200, max(1, int(page_size)))

    stmt: Select = (
        select(AuditLog, User.username)
        .select_from(AuditLog)
        .join(User, User.id == AuditLog.actor_user_id, isouter=True)
        .order_by(AuditLog.id.desc())
    )

    q_value = (q or "").strip()
    if q_value:
        stmt = stmt.where(
            or_(
                AuditLog.action.contains(q_value),
                AuditLog.target_type.contains(q_value),
                AuditLog.target_id.contains(q_value),
                AuditLog.detail.contains(q_value),
                User.username.contains(q_value),
            )
        )

    action_value = (action or "").strip()
    if action_value:
        stmt = stmt.where(AuditLog.action == action_value)

    if success is not None:
        stmt = stmt.where(AuditLog.success.is_(bool(success)))

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.offset((page - 1) * page_size).limit(page_size)).all()
    return rows, int(total)


def get_or_create_scheduler_setting(db: Session) -> AuditLogSchedulerSetting:
    setting = db.query(AuditLogSchedulerSetting).first()
    if setting:
        return setting
    setting = AuditLogSchedulerSetting()
    db.add(setting)
    db.flush()
    return setting


def purge_old_audit_logs(db: Session, *, cutoff: datetime) -> int:
    result = db.execute(delete(AuditLog).where(AuditLog.created_at < cutoff))
    return result.rowcount
