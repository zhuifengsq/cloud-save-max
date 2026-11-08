"""Legacy shim: new path app.application.telegram_bot.config (S4-6, 2026-11-09). Remove one release later."""
from app.application.telegram_bot.config import (  # noqa: F401
    TelegramBotConfig,
    load_telegram_bot_config,
)
