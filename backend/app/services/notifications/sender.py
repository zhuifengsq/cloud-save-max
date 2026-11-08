"""Legacy shim: new path app.application.notifications.sender (S4-6, 2026-11-09). Remove one release later."""
from app.application.notifications.sender import (  # noqa: F401
    logger,
    _get_channel_enabled_map,
    send_test,
    _should_send,
    send_runtime,
)
