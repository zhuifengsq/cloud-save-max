"""Bounded interactive auth sessions with independent adapter ownership."""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Lock, RLock
from typing import Any, Iterator
from app.core.errors import ApiError

logger = logging.getLogger(__name__)

@dataclass
class DriveAccountAuthSession:
    session_id: str
    account_id: int
    drive_type: str
    method: str
    adapter: Any
    payload: dict[str, Any]
    expires_at: float
    row_version: int | None = None
    completed_config: dict[str, Any] | None = None
    challenge_completed: bool = False
    operation_lock: RLock = field(default_factory=RLock, repr=False)
    owns_adapter: bool = False
    retired: bool = False
    active_operations: int = 0
    state_lock: Lock = field(default_factory=Lock, repr=False)

    @contextmanager
    def operation(self) -> Iterator[None]:
        """An active operation pins its transport until deferred retirement drains."""
        with self.operation_lock:
            with self.state_lock:
                self.active_operations += 1
            try:
                yield
            finally:
                with self.state_lock:
                    self.active_operations -= 1
                    close = self.retired and self.active_operations == 0 and self.owns_adapter
                    if close:
                        self.owns_adapter = False
                if close:
                    _close_adapter(self)

_LOCK = Lock()
_SESSIONS: dict[str, DriveAccountAuthSession] = {}
_MAX_SESSIONS = 512
_ENABLED = True


def _close_adapter(session: DriveAccountAuthSession) -> None:
    try:
        session.adapter.close()
    except Exception:
        logger.warning('Authentication adapter cleanup failed')


def _close_session(session: DriveAccountAuthSession) -> None:
    # State accounting is independent of the long-lived operation lock. Retire
    # and operation exit decide ownership atomically, without cross-session waits.
    with session.state_lock:
        session.retired = True
        close = session.active_operations == 0 and session.owns_adapter
        if close:
            session.owns_adapter = False
    if close:
        _close_adapter(session)


def _purge_expired_locked(now: float) -> list[DriveAccountAuthSession]:
    expired = [item for item in _SESSIONS.values() if item.expires_at <= now]
    for item in expired:
        _SESSIONS.pop(item.session_id, None)
    return expired


def create_auth_session(*, account_id: int, drive_type: str, method: str,
                        adapter: Any, payload: dict[str, Any], ttl_seconds: int = 600,
                        row_version: int | None = None) -> DriveAccountAuthSession:
    now = time.time()
    retain = getattr(adapter, 'retain', None)
    owns_adapter = False
    retained = adapter
    if callable(retain):
        retained = retain()
        owns_adapter = True
    session = DriveAccountAuthSession(session_id=uuid.uuid4().hex, account_id=account_id,
        drive_type=drive_type, method=method, adapter=retained, payload=dict(payload or {}),
        expires_at=now + ttl_seconds, row_version=row_version, owns_adapter=owns_adapter)
    with _LOCK:
        accepted = _ENABLED
        retired = _purge_expired_locked(now) if accepted else [session]
        if accepted:
            if len(_SESSIONS) >= _MAX_SESSIONS:
                overflow = len(_SESSIONS) - _MAX_SESSIONS + 1
                for sid, old in sorted(_SESSIONS.items(), key=lambda kv: kv[1].expires_at)[:overflow]:
                    _SESSIONS.pop(sid, None)
                    retired.append(old)
            _SESSIONS[session.session_id] = session
    for old in retired:
        _close_session(old)
    if not accepted:
        raise ApiError('DRIVE_ACCOUNT_AUTH_STOPPING', '服务正在关闭，请稍后重试', 503)
    return session


def get_auth_session(session_id: str) -> DriveAccountAuthSession | None:
    with _LOCK:
        session = _SESSIONS.get(session_id)
        if session is None:
            return None
        if session.expires_at > time.time():
            return session
        _SESSIONS.pop(session_id, None)
    _close_session(session)
    return None


def delete_auth_session(session_id: str) -> None:
    with _LOCK:
        session = _SESSIONS.pop(session_id, None)
    if session is not None:
        _close_session(session)


def clear_auth_sessions() -> None:
    """Retire outstanding challenges when the application lifespan ends."""
    with _LOCK:
        retired = list(_SESSIONS.values())
        _SESSIONS.clear()
    for session in retired:
        _close_session(session)


def startup_auth_sessions() -> None:
    global _ENABLED
    with _LOCK:
        _ENABLED = True


def shutdown_auth_sessions() -> None:
    global _ENABLED
    with _LOCK:
        _ENABLED = False
        retired = list(_SESSIONS.values())
        _SESSIONS.clear()
    for session in retired:
        _close_session(session)
