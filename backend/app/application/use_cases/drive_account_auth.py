"""Interactive authentication shared by HTTP and Telegram.

A successful cloud challenge is cached until the caller commits credentials and
its audit entry. Failed database writes can retry without resubmitting the code.
"""
from dataclasses import dataclass
from typing import Any, Callable, cast

from app.application import drive_account_auth_sessions as sessions
from app.core.drive_auth import DriveAuthRequired
from app.core.errors import bad_request, not_found
from app.domain.ports.drive_account import DriveAccountRecord
from app.domain.ports.drive_account_auth import AccountAuthGateway, ChallengeAdapter, QrcodeAuthProvider

@dataclass
class AuthResult:
    session: sessions.DriveAccountAuthSession
    account: DriveAccountRecord | None = None
    message: str = ""

class DriveAccountAuthUseCase:
    def __init__(self, *, provider: QrcodeAuthProvider, accounts: AccountAuthGateway) -> None:
        self.provider = provider
        self.accounts = accounts

    def status(self, session_id: str, method: str | None = None) -> sessions.DriveAccountAuthSession:
        session = sessions.get_auth_session(session_id)
        if session is None:
            raise not_found('DRIVE_ACCOUNT_AUTH_SESSION_NOT_FOUND', '认证会话已失效')
        if method is not None and session.method != method:
            raise bad_request('DRIVE_ACCOUNT_AUTH_METHOD_MISMATCH', '认证方式不匹配')
        return session

    def start_qrcode(self, db: Any, account_id: int) -> sessions.DriveAccountAuthSession:
        account = self.accounts.get(db, account_id)
        identity, drive_type = account.id, account.drive_type
        if drive_type != 'aliyun':
            raise bad_request('DRIVE_ACCOUNT_AUTH_UNSUPPORTED', '当前账号不支持扫码认证')
        version = self.accounts.prepare(db, identity)
        response = self.provider.generate_qrcode()
        data = self._response(response, '生成二维码失败')
        return sessions.create_auth_session(account_id=identity, drive_type=drive_type, method='qrcode',
            adapter={'t': data.get('t') or '', 'ck': data.get('ck') or ''},
            payload={'qrcode_url': data.get('qrCodeUrl') or '', 'status': 'NEW'}, row_version=version)

    @staticmethod
    def _response(response: dict[str, Any], message: str) -> dict[str, Any]:
        if not isinstance(response, dict) or not response.get('success'):
            detail = str(response.get('message') or '') if isinstance(response, dict) else ''
            raise bad_request('DRIVE_ACCOUNT_AUTH_FAILED', message, detail=detail)
        return cast(dict[str, Any], response.get('data') or {})

    def _complete(self, db: Any, session: sessions.DriveAccountAuthSession, version: int,
                  persist: Callable[[DriveAccountRecord], None]) -> AuthResult:
        account = self.accounts.probe(db, session.account_id, config=session.completed_config,
            auth_completed=session.method == 'sms', expected_version=version)
        persist(account)
        sessions.delete_auth_session(session.session_id)
        return AuthResult(session, account)

    def poll_qrcode(self, db: Any, session_id: str, *,
                    persist: Callable[[DriveAccountRecord], None]) -> AuthResult:
        session = self.status(session_id, 'qrcode')
        with session.operation():
            self.status(session_id, 'qrcode')
            version = self.accounts.prepare(db, session.account_id, session.row_version)
            if session.completed_config is None:
                meta = cast(dict[str, Any], session.adapter or {})
                data = self._response(self.provider.query_qrcode_status(str(meta.get('t') or ''),
                    str(meta.get('ck') or '')), '查询二维码状态失败')
                if str(data.get('status') or '') != 'CONFIRMED' or not data.get('refresh_token'):
                    session.payload.update(status=str(data.get('status') or ''), message=str(data.get('message') or ''))
                    return AuthResult(session)
                session.completed_config = {'refresh_token': str(data['refresh_token'])}
            return self._complete(db, session, version, persist)

    def send_sms(self, db: Any, session_id: str) -> object:
        session = self.status(session_id, 'sms')
        with session.operation():
            self.status(session_id, 'sms')
            self.accounts.prepare(db, session.account_id, session.row_version)
            return cast(ChallengeAdapter, session.adapter).send_sms()

    def submit_code(self, db: Any, session_id: str, code: str, *,
                    persist: Callable[[DriveAccountRecord], None], method: str | None = None) -> AuthResult:
        session = self.status(session_id, method)
        if session.method not in ('captcha', 'sms'):
            raise bad_request('DRIVE_ACCOUNT_AUTH_METHOD_MISMATCH', '认证方式不匹配')
        with session.operation():
            self.status(session_id, method)
            version = self.accounts.prepare(db, session.account_id, session.row_version)
            adapter = cast(ChallengeAdapter, session.adapter)
            if not session.challenge_completed:
                try:
                    if session.method == 'captcha':
                        adapter.submit_captcha(code)
                    else:
                        adapter.submit_sms(code)
                except DriveAuthRequired as exc:
                    new_session = sessions.create_auth_session(account_id=session.account_id,
                        drive_type=session.drive_type, method=exc.method, adapter=exc.adapter or adapter,
                        payload=exc.payload, row_version=version)
                    sessions.delete_auth_session(session_id)
                    return AuthResult(new_session, message=exc.message)
                session.challenge_completed = True
            if session.completed_config is None:
                session.completed_config = adapter.export_runtime_config()
            return self._complete(db, session, version, persist)
