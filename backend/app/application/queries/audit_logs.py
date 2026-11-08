"""审计日志读模型（application/queries）。

路由原先内联把 (row, username) 拼成 AuditLogItemOut；现在视图层只取需要的字段，
响应模型由 api/presenters/audit_log.py 负责（presentation 可依赖 application，反向不允许）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.application.audit_logs import list_audit_logs


@dataclass(frozen=True)
class AuditLogView:
    id: int
    actor_user_id: int | None
    actor_username: str | None
    action: str
    target_type: str | None
    target_id: str | None
    ip: str | None
    user_agent: str | None
    success: bool
    detail: str | None
    created_at: datetime


def _view(row: Any, username: str | None) -> AuditLogView:
    return AuditLogView(
        id=int(row.id),
        actor_user_id=row.actor_user_id,
        actor_username=username,
        action=str(row.action),
        target_type=row.target_type,
        target_id=row.target_id,
        ip=row.ip,
        user_agent=row.user_agent,
        success=bool(row.success),
        detail=row.detail,
        created_at=row.created_at,
    )


def list_audit_log_views(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 20,
    q: str | None = None,
    action: str | None = None,
    success: bool | None = None,
) -> "tuple[list[AuditLogView], int]":
    rows, total = list_audit_logs(db, page=page, page_size=page_size, q=q, action=action, success=success)
    return ([_view(row, username) for row, username in rows], int(total))
