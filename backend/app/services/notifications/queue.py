"""Legacy shim: new path app.application.notifications.queue (S4-6, 2026-11-09). Remove one release later."""
from app.application.notifications.queue import (  # noqa: F401
    logger,
    DEFAULT_MAXSIZE,
    NotifyHandler,
    NotifyQueue,
)
