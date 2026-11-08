"""TMDB 缓存调度设置仓储。"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.infrastructure.persistence.models.tmdb_cache_scheduler_setting import TMDBCacheSchedulerSetting

DEFAULT_TMDB_CACHE_SCHEDULER_SETTING_ID = 1


def get_setting(db: Session) -> Any:
    return db.get(TMDBCacheSchedulerSetting, DEFAULT_TMDB_CACHE_SCHEDULER_SETTING_ID)


def get_or_create_setting(db: Session) -> Any:
    item = db.get(TMDBCacheSchedulerSetting, DEFAULT_TMDB_CACHE_SCHEDULER_SETTING_ID)
    if item is not None:
        return item
    item = TMDBCacheSchedulerSetting(id=DEFAULT_TMDB_CACHE_SCHEDULER_SETTING_ID)
    db.add(item)
    db.flush()
    return item
