from __future__ import annotations

from typing import Any
from sqlalchemy.orm import Session
from app.application.telegram_bot.actions import TelegramBotActions
from app.application.telegram_bot.client import TelegramBotClient
from app.application.telegram_bot.config import TelegramBotConfig


from dataclasses import dataclass, field
from typing import Protocol

class BotTransportPort(Protocol):
    def actions(self, db: Session) -> TelegramBotActions:
        ...

    def allowed(self, chat_id: int, user_id: int) -> bool:
        ...

    def send(self, chat_id: int, text: str, *, reply_markup: dict[str, Any] | None=None) -> int | None:
        ...

    def send_photo(self, chat_id: int, photo_bytes: bytes, *, caption: str | None=None) -> int | None:
        ...

    def edit_or_send(self, chat_id: int, text: str, *, message_id: int | None=None, reply_markup: dict[str, Any] | None=None) -> int | None:
        ...

    def answer_callback(self, callback_query_id: str, text: str | None=None) -> None:
        ...

    def show_home(self, db: Session, chat_id: int, *, message_id: int | None=None) -> None:
        ...

    def show_status(self, db: Session, chat_id: int, *, message_id: int | None=None) -> None:
        ...

    def show_back_cancel_home(self, chat_id: int, text: str, *, message_id: int | None=None) -> None:
        ...

class TaskHandlersPort(Protocol):
    def show_tasks(self, db: Session, chat_id: int, *, page: int=1, _task_type: str | None=None, message_id: int | None=None) -> None:
        ...

    def show_task_detail(self, db: Session, chat_id: int, task_id: int, *, message_id: int | None=None, back_page: int=1, task_type: str | None=None) -> None:
        ...

    def dispatch_task(self, db: Session, chat_id: int, message_id: int, parts: list[str]) -> None:
        ...

    def dispatch_confirm(self, db: Session, chat_id: int, message_id: int, parts: list[str]) -> None:
        ...

    def run_async(self, chat_id: int, message_id: int, task_id: int, pending_text: str, job, success_title: str) -> None:
        ...

class AccountHandlersPort(Protocol):
    def show_accounts(self, db: Session, chat_id: int, *, page: int=1, message_id: int | None=None) -> None:
        ...

    def show_account_detail(self, db: Session, chat_id: int, account_id: int, *, message_id: int | None=None, back_page: int=1) -> None:
        ...

    def show_account_auth(self, db: Session, chat_id: int, auth_payload: dict[str, Any], *, message_id: int | None=None, back_page: int=1) -> None:
        ...

    def dispatch_account(self, db: Session, chat_id: int, message_id: int, parts: list[str]) -> None:
        ...

    def dispatch_auth(self, db: Session, chat_id: int, message_id: int, parts: list[str]) -> None:
        ...

    def handle_auth_code_input(self, db: Session, chat_id: int, text: str, session) -> None:
        ...

class SettingsHandlersPort(Protocol):
    def show_settings_menu(self, db: Session, chat_id: int, *, message_id: int | None=None) -> None:
        ...

    def show_setting_domain(self, db: Session, chat_id: int, domain_key: str, *, page: int=1, message_id: int | None=None) -> None:
        ...

    def dispatch_settings(self, db: Session, chat_id: int, message_id: int, parts: list[str]) -> None:
        ...

    def handle_settings_field_click(self, db: Session, chat_id: int, message_id: int, domain: str, field: str) -> None:
        ...

class SearchHandlersPort(Protocol):
    def show_search_prompt(self, db: Session, chat_id: int, *, replace_task_id: int | None=None, message_id: int | None=None) -> None:
        ...

    def show_search_results(self, db: Session, chat_id: int, *, keyword: str, results: list[dict[str, Any]], page: int=1, replace_task_id: int | None=None, message_id: int | None=None) -> None:
        ...

    def show_task_tmdb_prompt(self, db: Session, chat_id: int, *, message_id: int | None=None) -> None:
        ...

    def show_tmdb_results(self, db: Session, chat_id: int, *, keyword: str, items: list[dict[str, Any]], page: int=1, replace_task_id: int | None=None, message_id: int | None=None, message: str | None=None, bind_mode: bool=False) -> None:
        ...

    def run_search_resources(self, db: Session, chat_id: int, *, input_keyword: str, resource_keyword: str, replace_task_id: int | None, message_id: int | None, selected_tmdb: dict[str, Any] | None=None) -> None:
        ...

    def show_share_candidate_picker(self, db: Session, chat_id: int, *, text: str, candidates: list[dict[str, Any]], session, message_id: int | None=None) -> None:
        ...

    def apply_share_text(self, db: Session, chat_id: int, text: str, session, *, selected_shareurl: str | None=None) -> bool:
        ...

    def handle_share_text(self, db: Session, chat_id: int, text: str, session) -> bool:
        ...

    def dispatch_tmdb_results(self, db: Session, chat_id: int, message_id: int, parts: list[str]) -> None:
        ...

    def dispatch_search_results(self, db: Session, chat_id: int, message_id: int, parts: list[str]) -> None:
        ...

    def dispatch_share_picker(self, db: Session, chat_id: int, message_id: int, parts: list[str]) -> None:
        ...

class BotDispatchPort(Protocol):
    def handle_update(self, update: dict[str, Any]) -> None:
        ...

    def handle_message(self, message: dict[str, Any]) -> None:
        ...

    def handle_command(self, db: Session, chat_id: int, text: str, message_id: int | None) -> None:
        ...

    def handle_callback(self, callback_query: dict[str, Any]) -> None:
        ...

    def dispatch_callback(self, db: Session, chat_id: int, message_id: int, parts: list[str]) -> None:
        ...

class EditorHandlersPort(Protocol):
    def resume_task_editor(self, db: Session, chat_id: int, *, ctx: dict[str, Any], message_id: int | None=None) -> None:
        ...

    def resume_editor(self, db: Session, chat_id: int, *, kind: str, ctx: dict[str, Any], message_id: int | None=None) -> None:
        ...

    def show_task_drive_browser(self, db: Session, chat_id: int, *, message_id: int | None=None) -> None:
        ...

    def show_task_account_selector(self, db: Session, chat_id: int, *, message_id: int | None=None) -> None:
        ...

    def show_task_magic_rule_selector(self, db: Session, chat_id: int, *, message_id: int | None=None) -> None:
        ...

    def task_preview_kwargs(self, draft: dict[str, Any]) -> dict[str, Any]:
        ...

    def decorate_task_draft(self, draft: dict[str, Any], *, ctx: dict[str, Any] | None=None) -> dict[str, Any]:
        ...

    def auto_resolve_task_share_folder(self, db: Session, draft: dict[str, Any]) -> str | None:
        ...

    def apply_task_share_autofill(self, db: Session, draft: dict[str, Any], *, raw_text: str | None=None, selected_shareurl: str | None=None) -> tuple[dict[str, Any], str | None]:
        ...

    def show_task_share_browser(self, db: Session, chat_id: int, *, message_id: int | None=None) -> None:
        ...

    def start_editor(self, db: Session, chat_id: int, *, kind: str, title: str, fields: list[dict[str, Any]], draft: dict[str, Any], target_id: int | None=None, message_id: int | None=None, extras: dict[str, Any] | None=None) -> None:
        ...

    def dispatch_browser(self, db: Session, chat_id: int, message_id: int, parts: list[str]) -> None:
        ...

    def dispatch_back(self, db: Session, chat_id: int, message_id: int) -> None:
        ...

    def dispatch_task_account_selector(self, db: Session, chat_id: int, message_id: int, parts: list[str]) -> None:
        ...

    def dispatch_task_magic_rule_selector(self, db: Session, chat_id: int, message_id: int, parts: list[str]) -> None:
        ...

    def handle_editor_field_click(self, db: Session, chat_id: int, message_id: int, *, kind: str, fields: list[dict[str, Any]], target_id: int | None, field: str) -> None:
        ...

    def handle_editor_save(self, db: Session, chat_id: int, message_id: int, *, kind: str, fields: list[dict[str, Any]], target_id: int | None) -> None:
        ...

    def handle_field_input(self, db: Session, chat_id: int, user_id: int, text: str, session) -> None:
        ...


@dataclass
class BotContext:
    client: TelegramBotClient
    config: TelegramBotConfig
    transport: BotTransportPort = field(init=False)
    task_handlers: TaskHandlersPort = field(init=False)
    account_handlers: AccountHandlersPort = field(init=False)
    settings_handlers: SettingsHandlersPort = field(init=False)
    search_handlers: SearchHandlersPort = field(init=False)
    dispatch: BotDispatchPort = field(init=False)
    editor: EditorHandlersPort = field(init=False)
