from __future__ import annotations

from sqlalchemy.orm import Session

from app.application.container import audit_repo


def write_audit_log(
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
    """写审计日志（构造与落库细节在 infrastructure 仓储）。"""
    audit_repo().add_audit_log(
        db,
        actor_user_id=actor_user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        ip=ip,
        user_agent=user_agent,
        success=success,
        detail=detail,
    )
