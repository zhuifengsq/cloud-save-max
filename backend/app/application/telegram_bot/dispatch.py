from __future__ import annotations

from typing import Any
from sqlalchemy.orm import Session
from app.application.container import db_session
from app.application.telegram_bot.callbacks import parse_callback
from app.application.telegram_bot.formatters import button, keyboard, tmdb_loading_message
from app.application.telegram_bot.session_store import load_session_data, reset_session
from app.application.telegram_bot.context import BotContext
import logging
logger = logging.getLogger(__name__)


class BotDispatch:
    def __init__(self, context: BotContext):
        self.context = context

    def handle_update(self, update: dict[str, Any]) -> None:
        if isinstance(update.get("message"), dict):
            self.handle_message(update["message"])
            return
        if isinstance(update.get("callback_query"), dict):
            self.handle_callback(update["callback_query"])

    def handle_message(self, message: dict[str, Any]) -> None:
        chat_id = int(((message.get("chat") or {}).get("id")) or 0)
        user_id = int(((message.get("from") or {}).get("id")) or 0)
        if not self.context.transport.allowed(chat_id, user_id):
            return
        text = str(message.get("text") or "").strip()
        with db_session() as db:
            session = load_session_data(db, chat_id=chat_id, user_id=user_id)
            if text.startswith("/"):
                self.handle_command(db, chat_id, text, session.last_message_id)
                return
            if session.step == "await_keyword":
                if self.context.search_handlers.handle_share_text(db, chat_id, text, session):
                    return
                replace_task_id = int((session.context.get("replace_task_id") or 0) or 0) or None
                selected_tmdb: dict[str, Any] = {}
                if int(session.context.get("tmdb_id") or 0) > 0 and str(session.context.get("tmdb_media_type") or "").strip():
                    selected_tmdb = {
                        "id": int(session.context.get("tmdb_id") or 0),
                        "media_type": str(session.context.get("tmdb_media_type") or ""),
                        "display_title": str(session.context.get("tmdb_title") or text),
                        "standard_keyword": str(session.context.get("tmdb_title") or text),
                    }
                    self.context.search_handlers.run_search_resources(
                        db,
                        chat_id,
                        input_keyword=text,
                        resource_keyword=str(selected_tmdb.get("standard_keyword") or text),
                        replace_task_id=replace_task_id,
                        message_id=session.last_message_id,
                        selected_tmdb=selected_tmdb,
                    )
                    return
                loading_mid = self.context.transport.edit_or_send(
                    chat_id,
                    tmdb_loading_message(text, replace_mode=bool(replace_task_id)),
                    message_id=session.last_message_id,
                    reply_markup=keyboard([[button("✖️ 取消", "cancel"), button("🏠 首页", "home")]]),
                )
                result = self.context.transport.actions(db).search_tmdb_media(text)
                if bool(result.get("configured")) and list(result.get("items") or []):
                    self.context.search_handlers.show_tmdb_results(
                        db,
                        chat_id,
                        keyword=text,
                        items=list(result.get("items") or []),
                        page=1,
                        replace_task_id=replace_task_id,
                        message_id=loading_mid or session.last_message_id,
                    )
                else:
                    self.context.search_handlers.run_search_resources(
                        db,
                        chat_id,
                        input_keyword=text,
                        resource_keyword=text,
                        replace_task_id=replace_task_id,
                        message_id=loading_mid or session.last_message_id,
                    )
                return
            if session.step == "await_field_input":
                self.context.editor.handle_field_input(db, chat_id, user_id, text, session)
                return
            if session.step == "await_auth_code":
                self.context.account_handlers.handle_auth_code_input(db, chat_id, text, session)
                return
            if session.step == "await_task_tmdb_keyword":
                self.context.transport.edit_or_send(
                    chat_id,
                    tmdb_loading_message(text, replace_mode=False),
                    message_id=session.last_message_id,
                    reply_markup=keyboard([[button("✖️ 取消", "cancel"), button("🏠 首页", "home")]]),
                )
                result = self.context.transport.actions(db).search_tmdb_media(text)
                self.context.search_handlers.show_tmdb_results(
                    db,
                    chat_id,
                    keyword=text,
                    items=list(result.get("items") or []),
                    page=1,
                    message_id=session.last_message_id,
                    message=str(result.get("message") or "").strip() or None,
                    bind_mode=True,
                )
                return
            if self.context.search_handlers.handle_share_text(db, chat_id, text, session):
                return
            self.context.transport.show_home(db, chat_id, message_id=session.last_message_id)

    def handle_command(self, db: Session, chat_id: int, text: str, message_id: int | None) -> None:
        cmd = text.split()[0].lower()
        if cmd in {"/start", "/help", "/menu"}:
            self.context.transport.show_home(db, chat_id, message_id=message_id)
        elif cmd == "/tasks":
            self.context.task_handlers.show_tasks(db, chat_id, page=1, message_id=message_id)
        elif cmd == "/accounts":
            self.context.account_handlers.show_accounts(db, chat_id, page=1, message_id=message_id)
        elif cmd == "/search":
            self.context.search_handlers.show_search_prompt(db, chat_id, message_id=message_id)
        elif cmd == "/settings":
            self.context.settings_handlers.show_settings_menu(db, chat_id, message_id=message_id)
        elif cmd == "/status":
            self.context.transport.show_status(db, chat_id, message_id=message_id)
        elif cmd == "/cancel":
            reset_session(db, chat_id=chat_id, user_id=self.context.config.user_id, preserve_message_id=False)
            db.commit()
            self.context.transport.show_home(db, chat_id, message_id=None)
        else:
            self.context.transport.send(chat_id, "未知命令，可使用 /menu 返回主菜单。")

    def handle_callback(self, callback_query: dict[str, Any]) -> None:
        data = str(callback_query.get("data") or "")
        parts = parse_callback(data)
        callback_id = str(callback_query.get("id") or "")
        message = callback_query.get("message") or {}
        chat_id = int(((message.get("chat") or {}).get("id")) or 0)
        message_id = int(message.get("message_id") or 0)
        user_id = int(((callback_query.get("from") or {}).get("id")) or 0)
        if not self.context.transport.allowed(chat_id, user_id):
            self.context.transport.answer_callback(callback_id, "无权限")
            return
        with db_session() as db:
            try:
                self.dispatch_callback(db, chat_id, message_id, parts)
                self.context.transport.answer_callback(callback_id)
            except Exception as exc:
                logger.exception("telegram callback failed parts=%s", parts)
                self.context.transport.answer_callback(callback_id, str(exc)[:100])
                self.context.transport.send(chat_id, f"处理失败: {exc}")

    def dispatch_callback(self, db: Session, chat_id: int, message_id: int, parts: list[str]) -> None:
        if not parts:
            self.context.transport.show_home(db, chat_id, message_id=message_id)
            return
        head = parts[0]
        if head == "home":
            self.context.transport.show_home(db, chat_id, message_id=message_id)
            return
        if head == "cancel":
            reset_session(db, chat_id=chat_id, user_id=self.context.config.user_id, preserve_message_id=False)
            db.commit()
            self.context.transport.show_home(db, chat_id, message_id=message_id)
            return
        if head == "back":
            self.context.editor.dispatch_back(db, chat_id, message_id)
            return
        if head == "menu":
            target = parts[1] if len(parts) > 1 else "home"
            if target == "tasks":
                self.context.task_handlers.show_tasks(db, chat_id, page=1, message_id=message_id)
            elif target == "accounts":
                self.context.account_handlers.show_accounts(db, chat_id, page=1, message_id=message_id)
            elif target == "search":
                self.context.search_handlers.show_search_prompt(db, chat_id, message_id=message_id)
            elif target == "settings":
                self.context.settings_handlers.show_settings_menu(db, chat_id, message_id=message_id)
            elif target == "status":
                self.context.transport.show_status(db, chat_id, message_id=message_id)
            else:
                self.context.transport.show_home(db, chat_id, message_id=message_id)
            return
        if head == "tsk":
            self.context.task_handlers.dispatch_task(db, chat_id, message_id, parts[1:])
            return
        if head == "acc":
            self.context.account_handlers.dispatch_account(db, chat_id, message_id, parts[1:])
            return
        if head == "cfg":
            self.context.settings_handlers.dispatch_settings(db, chat_id, message_id, parts[1:])
            return
        if head == "sea":
            self.context.search_handlers.dispatch_search_results(db, chat_id, message_id, parts[1:])
            return
        if head == "tm":
            self.context.search_handlers.dispatch_tmdb_results(db, chat_id, message_id, parts[1:])
            return
        if head == "brw":
            self.context.editor.dispatch_browser(db, chat_id, message_id, parts[1:])
            return
        if head == "auth":
            self.context.account_handlers.dispatch_auth(db, chat_id, message_id, parts[1:])
            return
        if head == "asel":
            self.context.editor.dispatch_task_account_selector(db, chat_id, message_id, parts[1:])
            return
        if head == "rsel":
            self.context.editor.dispatch_task_magic_rule_selector(db, chat_id, message_id, parts[1:])
            return
        if head == "shr":
            self.context.search_handlers.dispatch_share_picker(db, chat_id, message_id, parts[1:])
            return
        if head == "confirm":
            self.context.task_handlers.dispatch_confirm(db, chat_id, message_id, parts[1:])
            return
        self.context.transport.show_home(db, chat_id, message_id=message_id)
