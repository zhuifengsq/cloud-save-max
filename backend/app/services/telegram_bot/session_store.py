"""Legacy shim: new path app.application.telegram_bot.session_store (S4-6, 2026-11-09). Remove one release later."""
from app.application.telegram_bot.session_store import (  # noqa: F401
    DEFAULT_TELEGRAM_BOT_STATE_ID,
    TelegramSessionData,
    _loads,
    _dumps,
    get_or_create_bot_state,
    get_last_update_id,
    save_last_update_id,
    mark_polled,
    mark_poll_error,
    get_or_create_session,
    load_session_data,
    save_session_data,
    reset_session,
)
