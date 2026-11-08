"""Legacy shim: new path app.application.notifications.settings (S4-6, 2026-11-09). Remove one release later."""
from app.application.notifications.settings import (  # noqa: F401
    DEFAULT_NOTIFICATION_SETTING_ID,
    get_or_create_notification_setting,
    load_notification_config,
    get_runtime_notification_config,
    update_notification_setting,
)
