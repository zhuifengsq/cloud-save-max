"""TMDB 设置仓储。"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.models.tmdb_setting import TMDBSetting

DEFAULT_TMDB_SETTING_ID = 1


def get_or_create_tmdb_setting(db: Session) -> Any:
    item = db.get(TMDBSetting, DEFAULT_TMDB_SETTING_ID)
    if item is not None:
        return item
    item = TMDBSetting(id=DEFAULT_TMDB_SETTING_ID, config_json=json.dumps({}, ensure_ascii=False))
    db.add(item)
    db.flush()
    return item
