from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.application.container import notification_settings_repo


DEFAULT_NOTIFICATION_SETTING_ID = 1


def get_or_create_notification_setting(db: Session) -> Any:
    return notification_settings_repo().get_or_create_notification_setting(db)


def load_notification_config(item: Any) -> dict[str, Any]:
    if not item.config_json:
        return {}
    try:
        payload = json.loads(item.config_json)
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    payload.pop("CONSOLE", None)
    return payload


def get_runtime_notification_config(db: Session) -> dict[str, Any]:
    item = get_or_create_notification_setting(db)
    return load_notification_config(item)


def update_notification_setting(
    db: Session,
    *,
    config: dict[str, Any] | None = None,
) -> Any:
    item = get_or_create_notification_setting(db)
    if config is not None:
        config.pop("CONSOLE", None)
        item.config_json = json.dumps(config, ensure_ascii=False)
    db.flush()
    return item
