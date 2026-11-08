from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.application.container import audit_repo


def get_or_create_audit_log_scheduler_setting(db: Session) -> Any:
    return audit_repo().get_or_create_scheduler_setting(db)


def update_audit_log_scheduler_setting(
    db: Session,
    *,
    enabled: bool | None = None,
    crontab: str | None = None,
    timezone: str | None = None,
    retention_days: int | None = None,
) -> Any:
    setting = audit_repo().get_or_create_scheduler_setting(db)
    if enabled is not None:
        setting.enabled = enabled
    if crontab is not None:
        setting.crontab = crontab
    if timezone is not None:
        setting.timezone = timezone
    if retention_days is not None:
        setting.retention_days = max(1, int(retention_days))
    db.flush()
    return setting


def purge_old_audit_logs(db: Session, *, retention_days: int = 30) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, retention_days))
    return audit_repo().purge_old_audit_logs(db, cutoff=cutoff)
