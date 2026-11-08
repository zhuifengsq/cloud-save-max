"""Telegram bot 状态与会话仓储。"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.telegram_bot_session import TelegramBotSession
from app.models.telegram_bot_state import TelegramBotState

DEFAULT_TELEGRAM_BOT_STATE_ID = 1


def get_or_create_bot_state(db: Session) -> Any:
    item = db.get(TelegramBotState, DEFAULT_TELEGRAM_BOT_STATE_ID)
    if item is not None:
        return item
    item = TelegramBotState(id=DEFAULT_TELEGRAM_BOT_STATE_ID, last_update_id=0)
    db.add(item)
    db.flush()
    return item


def get_or_create_session(db: Session, *, chat_id: int, user_id: int) -> Any:
    row = (
        db.query(TelegramBotSession)
        .filter(TelegramBotSession.chat_id == int(chat_id), TelegramBotSession.user_id == int(user_id))
        .first()
    )
    if row is not None:
        return row
    row = TelegramBotSession(chat_id=int(chat_id), user_id=int(user_id), scene="home", step="idle", context_json="{}")
    db.add(row)
    db.flush()
    return row
