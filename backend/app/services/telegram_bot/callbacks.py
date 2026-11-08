"""Legacy shim: new path app.application.telegram_bot.callbacks (S4-6, 2026-11-09). Remove one release later."""
from app.application.telegram_bot.callbacks import (  # noqa: F401
    cb,
    parse_callback,
)
