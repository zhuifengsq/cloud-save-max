"""通知设置仓储。"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.infrastructure.persistence.models.notification_setting import NotificationSetting

DEFAULT_NOTIFICATION_SETTING_ID = 1


def get_or_create_notification_setting(db: Session) -> Any:
    item = db.get(NotificationSetting, DEFAULT_NOTIFICATION_SETTING_ID)
    if item is not None:
        return item
    item = NotificationSetting(id=DEFAULT_NOTIFICATION_SETTING_ID, config_json=json.dumps({}, ensure_ascii=False))
    db.add(item)
    db.flush()
    return item
