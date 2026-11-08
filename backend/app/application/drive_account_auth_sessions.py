from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from threading import Lock
from typing import Any


@dataclass
class DriveAccountAuthSession:
    session_id: str
    account_id: int
    drive_type: str
    method: str
    adapter: Any
    payload: dict[str, Any]
    expires_at: float


_LOCK = Lock()
_SESSIONS: dict[str, DriveAccountAuthSession] = {}
# 扫码/验证码登录的会话只活 10 分钟。旧实现只在 get_auth_session 命中时才删除过期项，
# 用户中途放弃扫码的会话就永远留在字典里（每个会话还持有 adapter 实例 → 登录态、连接池、
# cookie 一起泄漏）。现在每次创建会话前顺手清一次过期项，保证「不再被访问」的会话也能回收。
_MAX_SESSIONS = 512


def _purge_expired_locked(now: float) -> int:
    expired = [sid for sid, item in _SESSIONS.items() if item.expires_at <= now]
    for sid in expired:
        _SESSIONS.pop(sid, None)
    return len(expired)


def create_auth_session(
    *,
    account_id: int,
    drive_type: str,
    method: str,
    adapter: Any,
    payload: dict[str, Any],
    ttl_seconds: int = 600,
) -> DriveAccountAuthSession:
    now = time.time()
    session_id = uuid.uuid4().hex
    session = DriveAccountAuthSession(
        session_id=session_id,
        account_id=account_id,
        drive_type=drive_type,
        method=method,
        adapter=adapter,
        payload=dict(payload or {}),
        expires_at=now + ttl_seconds,
    )
    with _LOCK:
        _purge_expired_locked(now)
        # 上限保护：极端情况下（长期不重启 + 大量未完成扫码）仍可能堆积，超过上限时
        # 淘汰最早到期的一批，避免字典无界增长（会话本身有 TTL，淘汰不影响已完成的登录）。
        if len(_SESSIONS) >= _MAX_SESSIONS:
            overflow = len(_SESSIONS) - _MAX_SESSIONS + 1
            for sid, _ in sorted(_SESSIONS.items(), key=lambda kv: kv[1].expires_at)[:overflow]:
                _SESSIONS.pop(sid, None)
        _SESSIONS[session_id] = session
    return session


def get_auth_session(session_id: str) -> DriveAccountAuthSession | None:
    now = time.time()
    with _LOCK:
        session = _SESSIONS.get(session_id)
        if session is None:
            return None
        if session.expires_at <= now:
            _SESSIONS.pop(session_id, None)
            return None
        return session


def delete_auth_session(session_id: str) -> None:
    with _LOCK:
        _SESSIONS.pop(session_id, None)

