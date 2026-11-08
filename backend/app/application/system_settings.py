"""系统设置读写服务（settings 键值 + 默认值）。

S5：路由与服务共用同一套默认值与读写逻辑，避免 application 反向 import 路由模块。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.application.container import system_settings_repo


SETTINGS_DEFAULTS: dict[str, str] = {
    "preferred_sharers": "",
    "blocked_sharers": "",
    "validate_batch_size": "5",
    "preview_cache_ttl_seconds": "300",
    "search_cache_ttl_seconds": "3",
}


def get_setting(db: Session, key: str, default: str | None = None) -> str:
    """读设置；default=None 时用 SETTINGS_DEFAULTS 里的约定默认值。"""
    fallback = SETTINGS_DEFAULTS.get(key, "") if default is None else default
    return system_settings_repo().get_value(db, key, fallback)


def set_setting(db: Session, key: str, value: str, description: str = "") -> None:
    system_settings_repo().set_value(db, key, value, description)
