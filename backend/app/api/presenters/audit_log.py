"""审计日志视图 → 响应模型（纯函数）。"""

from __future__ import annotations

from app.application.queries.audit_logs import AuditLogView
from app.schemas.audit_log import AuditLogItemOut, AuditLogListOut


def audit_log_item(view: AuditLogView) -> AuditLogItemOut:
    return AuditLogItemOut(
        id=view.id,
        actor_user_id=view.actor_user_id,
        actor_username=view.actor_username,
        action=view.action,
        target_type=view.target_type,
        target_id=view.target_id,
        ip=view.ip,
        user_agent=view.user_agent,
        success=view.success,
        detail=view.detail,
        created_at=view.created_at,
    )


def audit_log_list(
    views: "list[AuditLogView]",
    *,
    page: int,
    page_size: int,
    total: int,
) -> AuditLogListOut:
    return AuditLogListOut(
        page=page,
        page_size=page_size,
        total=total,
        items=[audit_log_item(view) for view in views],
    )
