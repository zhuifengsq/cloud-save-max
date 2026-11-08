from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.application.container import task_scheduler_setting_repo
from app.services.scheduler_validation import validate_scheduler_setting


DEFAULT_TASK_SCHEDULER_SETTING_ID = 1


def load_task_scheduler_setting(db: Session) -> Any:
    return task_scheduler_setting_repo().get_setting(db)


def get_or_create_task_scheduler_setting(db: Session) -> Any:
    return task_scheduler_setting_repo().get_or_create_setting(db)


def update_task_scheduler_setting(db: Session, *, enabled: bool | None = None, crontab: str | None = None, timezone: str | None = None) -> Any:
    setting = get_or_create_task_scheduler_setting(db)
    next_crontab = crontab if crontab is not None else getattr(setting, "crontab", None)
    next_timezone = timezone if timezone is not None else getattr(setting, "timezone", None)
    normalized_crontab, normalized_timezone = validate_scheduler_setting(next_crontab, next_timezone)
    if enabled is not None:
        setting.enabled = enabled
    setting.crontab = normalized_crontab
    setting.timezone = normalized_timezone
    db.flush()
    return setting
