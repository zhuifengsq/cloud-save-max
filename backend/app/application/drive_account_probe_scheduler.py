from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.application.container import drive_account_probe_scheduler_setting_repo
from app.application.scheduler_validation import validate_scheduler_setting


DEFAULT_DRIVE_ACCOUNT_PROBE_SCHEDULER_SETTING_ID = 1


def load_drive_account_probe_scheduler_setting(db: Session) -> Any:
    return drive_account_probe_scheduler_setting_repo().get_setting(db)


def get_or_create_drive_account_probe_scheduler_setting(db: Session) -> Any:
    return drive_account_probe_scheduler_setting_repo().get_or_create_setting(db)


def update_drive_account_probe_scheduler_setting(
    db: Session,
    *,
    enabled: bool | None = None,
    crontab: str | None = None,
    timezone: str | None = None,
    enabled_only: bool | None = None,
) -> Any:
    item = get_or_create_drive_account_probe_scheduler_setting(db)
    next_crontab = crontab if crontab is not None else getattr(item, "crontab", None)
    next_timezone = timezone if timezone is not None else getattr(item, "timezone", None)
    normalized_crontab, normalized_timezone = validate_scheduler_setting(next_crontab, next_timezone)
    if enabled is not None:
        item.enabled = enabled
    item.crontab = normalized_crontab
    item.timezone = normalized_timezone
    if enabled_only is not None:
        item.enabled_only = enabled_only
    db.flush()
    return item
