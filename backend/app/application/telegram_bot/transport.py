from __future__ import annotations

from typing import Any
from sqlalchemy.orm import Session
from app.application.telegram_bot.actions import TelegramBotActions
from app.application.telegram_bot.formatters import button, home_keyboard, home_message, keyboard, status_message
from app.application.telegram_bot.session_store import save_session_data
from app.application.telegram_bot.context import BotContext


class BotTransport:
    def __init__(self, context: BotContext):
        self.context = context

    def actions(self, db: Session) -> TelegramBotActions:
        return TelegramBotActions(db=db)

    def allowed(self, chat_id: int, user_id: int) -> bool:
        return int(chat_id) == int(self.context.config.user_id) and int(user_id) == int(self.context.config.user_id)

    def send(self, chat_id: int, text: str, *, reply_markup: dict[str, Any] | None = None) -> int | None:
        result = self.context.client.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
        message_id = result.get("message_id")
        return int(message_id) if isinstance(message_id, int) else None

    def send_photo(self, chat_id: int, photo_bytes: bytes, *, caption: str | None = None) -> int | None:
        result = self.context.client.send_photo(chat_id=chat_id, photo_bytes=photo_bytes, caption=caption)
        message_id = result.get("message_id")
        return int(message_id) if isinstance(message_id, int) else None

    def edit_or_send(self, chat_id: int, text: str, *, message_id: int | None = None, reply_markup: dict[str, Any] | None = None) -> int | None:
        if message_id:
            try:
                result = self.context.client.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=reply_markup)
                message_id = result.get("message_id", message_id)
                return int(message_id) if isinstance(message_id, int) else message_id
            except Exception as exc:
                if "message is not modified" in str(exc).lower():
                    return message_id
        return self.send(chat_id, text, reply_markup=reply_markup)

    def answer_callback(self, callback_query_id: str, text: str | None = None) -> None:
        try:
            self.context.client.answer_callback_query(callback_query_id=callback_query_id, text=text)
        except Exception:
            pass

    def show_home(self, db: Session, chat_id: int, *, message_id: int | None = None) -> None:
        mid = self.edit_or_send(chat_id, home_message(), message_id=message_id, reply_markup=home_keyboard())
        save_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id, scene="home", step="idle", context={}, last_message_id=mid)
        db.commit()

    def show_status(self, db: Session, chat_id: int, *, message_id: int | None = None) -> None:
        summary = self.actions(db).build_status_summary()
        mid = self.edit_or_send(chat_id, status_message(summary), message_id=message_id, reply_markup=keyboard([[button("🏠 首页", "home")]]))
        save_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id, scene="status", step="idle", context={}, last_message_id=mid)
        db.commit()

    def show_back_cancel_home(self, chat_id: int, text: str, *, message_id: int | None = None) -> None:
        self.edit_or_send(
            chat_id,
            text,
            message_id=message_id,
            reply_markup=keyboard(
                [
                    [button("⬅️ 返回", "back"), button("✖️ 取消", "cancel")],
                    [button("🏠 首页", "home")],
                ]
            ),
        )
