"""任务调度设置仓储。"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.task_scheduler_setting import TaskSchedulerSetting

DEFAULT_TASK_SCHEDULER_SETTING_ID = 1


def get_setting(db: Session) -> Any:
    return db.get(TaskSchedulerSetting, DEFAULT_TASK_SCHEDULER_SETTING_ID)


def get_or_create_setting(db: Session) -> Any:
    setting = (
        db.execute(select(TaskSchedulerSetting).where(TaskSchedulerSetting.id == DEFAULT_TASK_SCHEDULER_SETTING_ID))
        .scalars()
        .first()
    )
    if setting is not None:
        return setting
    setting = TaskSchedulerSetting(id=DEFAULT_TASK_SCHEDULER_SETTING_ID)
    db.add(setting)
    db.flush()
    return setting
