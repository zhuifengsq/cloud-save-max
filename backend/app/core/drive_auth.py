"""Authentication challenge shared by application logic and drive adapters."""
from typing import Any, Literal

AuthMethod = Literal['captcha', 'sms', 'qrcode']


class DriveAuthRequired(Exception):
    def __init__(self, *, method: AuthMethod, message: str,
                 payload: dict[str, Any] | None = None, adapter: object | None = None) -> None:
        super().__init__(message)
        self.method = method
        self.message = message
        self.payload = payload or {}
        self.adapter = adapter
