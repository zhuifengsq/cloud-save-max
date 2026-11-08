"""网盘账号探测调度设置仓储。"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.infrastructure.persistence.models.drive_account_probe_scheduler_setting import DriveAccountProbeSchedulerSetting

DEFAULT_ID = 1


def get_setting(db: Session) -> Any:
    return db.get(DriveAccountProbeSchedulerSetting, DEFAULT_ID)


def get_or_create_setting(db: Session) -> Any:
    item = db.get(DriveAccountProbeSchedulerSetting, DEFAULT_ID)
    if item is not None:
        return item
    item = DriveAccountProbeSchedulerSetting(id=DEFAULT_ID)
    db.add(item)
    db.flush()
    return item
