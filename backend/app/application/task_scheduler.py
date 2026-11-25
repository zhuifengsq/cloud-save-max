from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from app.application.container import clock, task_scheduler_setting_repo
from app.application.scheduler_validation import normalize_timezone, validate_scheduler_setting


DEFAULT_TASK_SCHEDULER_SETTING_ID = 1


def load_task_scheduler_setting(db: Session) -> Any:
    return task_scheduler_setting_repo().get_setting(db)


def task_schedule_time(timezone_name: str | None = None) -> datetime:
    schedule_zone = ZoneInfo(normalize_timezone(timezone_name))
    current_utc = clock().now_utc().replace(tzinfo=timezone.utc)
    return current_utc.astimezone(schedule_zone)


def configured_task_schedule_time(db: Session | None) -> datetime:
    setting = load_task_scheduler_setting(db) if db is not None else None
    try:
        return task_schedule_time(getattr(setting, "timezone", None))
    except (ZoneInfoNotFoundError, ValueError):
        return task_schedule_time()


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
