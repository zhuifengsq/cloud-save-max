from __future__ import annotations


from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.application.container import adapter_factory as _acc_AdapterFactory; AdapterFactory = _acc_AdapterFactory()
from app.application.container import account_manager_cls as _acc_DatabaseAccountManager; DatabaseAccountManager = _acc_DatabaseAccountManager()
from app.application.container import adapter_registry as _acc_AdapterRegistry; AdapterRegistry = _acc_AdapterRegistry()
from app.application.container import drive_accounts_repo, drive_account_auth_use_case
from app.application.drive_accounts import (
    create_drive_account,
    get_drive_account,
    list_drive_accounts,
    probe_drive_account,
    refresh_drive_account_profiles,
    serialize_drive_account,
    set_default_drive_account,
    set_drive_account_enabled,
    sign_in_drive_account,
    supported_drive_types,
    update_drive_account,
)


from app.application.telegram_bot.action_helpers import _loads_json_detail

@dataclass
class TelegramAccountActions:
    db: Session
    auth: Any = None

    def __post_init__(self):
        if self.auth is None:
            self.auth = drive_account_auth_use_case()

    def list_accounts(self, *, page: int = 1, page_size: int = 8) -> dict[str, Any]:
        page = max(1, int(page or 1))
        items = [serialize_drive_account(x) for x in list_drive_accounts(self.db)]
        total = len(items)
        start = (page - 1) * page_size
        sliced = items[start : start + page_size]
        return {"items": sliced, "total": total, "page": page, "page_size": page_size}


    def get_account_detail(self, account_id: int) -> dict[str, Any]:
        return serialize_drive_account(get_drive_account(self.db, int(account_id)))


    def set_account_default(self, account_id: int) -> dict[str, Any]:
        account = set_default_drive_account(self.db, int(account_id))
        self.db.commit()
        self.db.refresh(account)
        return serialize_drive_account(account)


    def toggle_account(self, account_id: int) -> dict[str, Any]:
        account = get_drive_account(self.db, int(account_id))
        if bool(account.enabled):
            account = set_drive_account_enabled(self.db, int(account_id), False)
        else:
            account = probe_drive_account(self.db, int(account_id))
            if getattr(account, "runtime_status", None) == "active":
                account.enabled = True
        self.db.commit()
        self.db.refresh(account)
        return serialize_drive_account(account)


    def probe_account(self, account_id: int) -> dict[str, Any]:
        account = probe_drive_account(self.db, int(account_id))
        self.db.commit()
        self.db.refresh(account)
        return serialize_drive_account(account)


    def sign_in_account(self, account_id: int) -> dict[str, Any]:
        result = sign_in_drive_account(self.db, int(account_id))
        self.db.commit()
        return result


    def refresh_accounts(self) -> list[dict[str, Any]]:
        items = refresh_drive_account_profiles(self.db)
        self.db.commit()
        for item in items:
            self.db.refresh(item)
        return [serialize_drive_account(x) for x in items]


    def save_account(self, *, account_id: int | None = None, payload: dict[str, Any]) -> dict[str, Any]:
        data = dict(payload or {})
        if account_id is None:
            account = create_drive_account(self.db, **data)
        else:
            account = update_drive_account(self.db, int(account_id), **data)
        self.db.commit()
        self.db.refresh(account)
        return serialize_drive_account(account)


    def get_drive_types(self) -> list[dict[str, Any]]:
        return supported_drive_types()


    def list_account_names(self) -> list[str]:
        rows = drive_accounts_repo().list_enabled_names(self.db)
        return [str(x) for x in rows if x]


    def start_account_auth(self, account_id: int) -> dict[str, Any]:
        account = get_drive_account(self.db, int(account_id))
        if str(account.drive_type or '') == 'aliyun':
            return self._auth_payload(self.auth.start_qrcode(self.db, int(account_id)))
        try:
            account = probe_drive_account(self.db, int(account_id))
            self.db.commit()
        except ApiError as exc:
            if exc.code == 'DRIVE_ACCOUNT_AUTH_REQUIRED':
                detail = _loads_json_detail(exc.detail)
                detail['message'] = exc.message
                return detail
            raise
        self.db.refresh(account)
        return {'account_id': int(account.id), 'drive_type': str(account.drive_type or ''),
                'method': 'done', 'account': serialize_drive_account(account)}


    @staticmethod
    def _auth_payload(session) -> dict[str, Any]:
        return {'account_id': int(session.account_id), 'drive_type': str(session.drive_type or ''),
                'method': str(session.method), 'session_id': str(session.session_id),
                'payload': dict(session.payload or {})}


    def get_account_auth_status(self, session_id: str) -> dict[str, Any]:
        return self._auth_payload(self.auth.status(session_id))


    def _persist_auth(self, account) -> None:
        self.db.commit()


    def poll_account_auth_qrcode(self, session_id: str) -> dict[str, Any]:
        result = self.auth.poll_qrcode(self.db, session_id, persist=self._persist_auth)
        if result.account is not None:
            self.db.refresh(result.account)
            return {'done': True, 'account': serialize_drive_account(result.account)}
        self.db.commit()
        return {'done': False, **self._auth_payload(result.session)}


    def send_account_auth_sms(self, session_id: str) -> dict[str, Any]:
        result = self.auth.send_sms(self.db, session_id)
        self.db.commit()
        return result if isinstance(result, dict) else {'ok': True, 'message': str(result)}


    def submit_account_auth_code(self, session_id: str, code: str) -> dict[str, Any]:
        result = self.auth.submit_code(self.db, session_id, code, persist=self._persist_auth)
        if result.account is None:
            return {'done': False, **self._auth_payload(result.session)}
        self.db.refresh(result.account)
        return {'done': True, 'account': serialize_drive_account(result.account)}

