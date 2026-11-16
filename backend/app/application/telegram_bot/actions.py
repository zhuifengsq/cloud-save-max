"""Telegram action facade composed from independent domain action handlers."""
from sqlalchemy.orm import Session
from app.application.telegram_bot.action_helpers import _to_sort_ts as _to_sort_ts
from app.application.telegram_bot.task_actions import TelegramTaskActions
from app.application.telegram_bot.resource_actions import TelegramResourceActions
from app.application.telegram_bot.account_actions import TelegramAccountActions
from app.application.telegram_bot.setting_actions import TelegramSettingActions
from typing import Any


class TelegramBotActions:
    def __init__(self, db: Session, *, auth=None):
        self.db = db
        self.tasks = TelegramTaskActions(db)
        self.resources = TelegramResourceActions(db)
        self.accounts = TelegramAccountActions(db, auth=auth)
        self.settings = TelegramSettingActions(db)

    def build_status_summary(self) -> dict[str, Any]:
        return self.tasks.build_status_summary()

    def list_tasks(self, *, page: int=1, page_size: int=8, task_type: str | None=None) -> dict[str, Any]:
        return self.tasks.list_tasks(page=page, page_size=page_size, task_type=task_type)

    def get_task_detail(self, task_id: int) -> dict[str, Any]:
        return self.tasks.get_task_detail(task_id)

    def toggle_task(self, task_id: int) -> dict[str, Any]:
        return self.tasks.toggle_task(task_id)

    def delete_task(self, task_id: int) -> None:
        return self.tasks.delete_task(task_id)

    def save_task(self, *, task_id: int | None=None, payload: dict[str, Any]) -> dict[str, Any]:
        return self.tasks.save_task(task_id=task_id, payload=payload)

    def run_task(self, task_id: int) -> dict[str, Any]:
        return self.tasks.run_task(task_id)

    def list_task_executions(self, task_id: int, *, limit: int=5) -> list[dict[str, Any]]:
        return self.tasks.list_task_executions(task_id, limit=limit)

    def replace_task_shareurl(self, task_id: int, shareurl: str, *, account_name: str | None=None, tmdb_id: int | None=None, tmdb_media_type: str | None=None) -> dict[str, Any]:
        return self.tasks.replace_task_shareurl(task_id, shareurl, account_name=account_name, tmdb_id=tmdb_id, tmdb_media_type=tmdb_media_type)

    def search_resources(self, keyword: str, *, drive_type: str | None=None) -> dict[str, Any]:
        return self.resources.search_resources(keyword, drive_type=drive_type)

    def inspect_share_text(self, text: str) -> dict[str, Any] | None:
        return self.resources.inspect_share_text(text)

    def extract_share_candidates(self, text: str) -> list[dict[str, Any]]:
        return self.resources.extract_share_candidates(text)

    def inspect_share_candidate(self, text: str, shareurl: str) -> dict[str, Any] | None:
        return self.resources.inspect_share_candidate(text, shareurl)

    def preview_task_share(self, *, shareurl: str, account_name: str | None=None, pdir_fid: str | None=None, max_items: int=200, taskname: str | None=None, pattern: str | None=None, replace: str | None=None, savepath: str | None=None, ignore_extension: bool | None=None, tmdb_id: int | None=None, tmdb_media_type: str | None=None) -> dict[str, Any]:
        return self.resources.preview_task_share(shareurl=shareurl, account_name=account_name, pdir_fid=pdir_fid, max_items=max_items, taskname=taskname, pattern=pattern, replace=replace, savepath=savepath, ignore_extension=ignore_extension, tmdb_id=tmdb_id, tmdb_media_type=tmdb_media_type)

    def search_tmdb_media(self, keyword: str, *, media_type: str='multi', year: str | None=None) -> dict[str, Any]:
        return self.resources.search_tmdb_media(keyword, media_type=media_type, year=year)

    def list_magic_regex_rules(self) -> list[dict[str, str]]:
        return self.resources.list_magic_regex_rules()

    def browse_task_drive(self, *, dir_path: str, account_name: str | None=None, shareurl: str | None=None, max_items: int=100) -> dict[str, Any]:
        return self.resources.browse_task_drive(dir_path=dir_path, account_name=account_name, shareurl=shareurl, max_items=max_items)

    def list_accounts(self, *, page: int=1, page_size: int=8) -> dict[str, Any]:
        return self.accounts.list_accounts(page=page, page_size=page_size)

    def get_account_detail(self, account_id: int) -> dict[str, Any]:
        return self.accounts.get_account_detail(account_id)

    def set_account_default(self, account_id: int) -> dict[str, Any]:
        return self.accounts.set_account_default(account_id)

    def toggle_account(self, account_id: int) -> dict[str, Any]:
        return self.accounts.toggle_account(account_id)

    def probe_account(self, account_id: int) -> dict[str, Any]:
        return self.accounts.probe_account(account_id)

    def sign_in_account(self, account_id: int) -> dict[str, Any]:
        return self.accounts.sign_in_account(account_id)

    def refresh_accounts(self) -> list[dict[str, Any]]:
        return self.accounts.refresh_accounts()

    def save_account(self, *, account_id: int | None=None, payload: dict[str, Any]) -> dict[str, Any]:
        return self.accounts.save_account(account_id=account_id, payload=payload)

    def get_drive_types(self) -> list[dict[str, Any]]:
        return self.accounts.get_drive_types()

    def list_account_names(self) -> list[str]:
        return self.accounts.list_account_names()

    def start_account_auth(self, account_id: int) -> dict[str, Any]:
        return self.accounts.start_account_auth(account_id)

    def get_account_auth_status(self, session_id: str) -> dict[str, Any]:
        return self.accounts.get_account_auth_status(session_id)

    def poll_account_auth_qrcode(self, session_id: str) -> dict[str, Any]:
        return self.accounts.poll_account_auth_qrcode(session_id)

    def send_account_auth_sms(self, session_id: str) -> dict[str, Any]:
        return self.accounts.send_account_auth_sms(session_id)

    def submit_account_auth_code(self, session_id: str, code: str) -> dict[str, Any]:
        return self.accounts.submit_account_auth_code(session_id, code)

    def list_setting_domains(self) -> list[dict[str, str]]:
        return self.settings.list_setting_domains()

    def get_setting_domain(self, key: str) -> dict[str, Any]:
        return self.settings.get_setting_domain(key)

    def update_setting_value(self, domain: str, field: str, value: Any) -> dict[str, Any]:
        return self.settings.update_setting_value(domain, field, value)

    def send_notification_test(self, title: str, content: str, channels: list[str] | None=None) -> list[dict[str, Any]]:
        return self.settings.send_notification_test(title, content, channels)

    def raw_notification_keys(self) -> list[str]:
        return self.settings.raw_notification_keys()
