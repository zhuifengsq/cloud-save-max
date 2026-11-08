"""认证上下文（application 层）：当前用户装载 + 角色权限进程内缓存。

S5 从 `app/core/deps.py` 抽出「与 Web 框架无关」的部分，使 platform 层不再直接依赖
persistence；FastAPI 依赖（OAuth2 scheme / require_permissions）留在 `app/api/deps.py`。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.application.container import users_repo
from app.core.errors import unauthorized
from app.core.security import decode_access_token
from app.core.settings import settings


@dataclass(frozen=True)
class CurrentUser:
    user: Any
    roles: list[str]
    permissions: set[str]


# 用户角色/权限查询的进程内缓存：每个已登录请求可省掉 2 条 JOIN 查询。
# 权限/角色变更时会主动失效，未覆盖到的直接改库场景最多滞后 permission_cache_ttl_seconds 秒。
_PERMISSION_CACHE_MAX_ENTRIES = 1024
_permission_cache: dict[int, tuple[float, tuple[str, ...], frozenset[str]]] = {}
_permission_cache_lock = threading.Lock()


def _permission_cache_ttl() -> float:
    try:
        return max(0.0, float(settings.permission_cache_ttl_seconds))
    except (TypeError, ValueError):
        return 0.0


def invalidate_permission_cache(user_id: int | None = None) -> None:
    with _permission_cache_lock:
        if user_id is None:
            _permission_cache.clear()
        else:
            _permission_cache.pop(int(user_id), None)


def _load_permissions_uncached(db: Session, user_id: int) -> tuple[list[str], set[str]]:
    return users_repo().load_roles_permissions(db, user_id)


def _load_permissions(db: Session, user_id: int) -> tuple[list[str], set[str]]:
    ttl = _permission_cache_ttl()
    if ttl <= 0:
        return _load_permissions_uncached(db, user_id)

    now = time.monotonic()
    with _permission_cache_lock:
        hit = _permission_cache.get(user_id)
    if hit is not None and hit[0] > now:
        return list(hit[1]), set(hit[2])

    roles, permissions = _load_permissions_uncached(db, user_id)

    with _permission_cache_lock:
        if len(_permission_cache) >= _PERMISSION_CACHE_MAX_ENTRIES:
            expired = [key for key, value in _permission_cache.items() if value[0] <= now]
            for key in expired:
                _permission_cache.pop(key, None)
            if len(_permission_cache) >= _PERMISSION_CACHE_MAX_ENTRIES:
                _permission_cache.clear()
        _permission_cache[user_id] = (now + ttl, tuple(roles), frozenset(permissions))

    return roles, permissions


def load_user_roles_permissions(db: Session, user_id: int) -> tuple[list[str], list[str]]:
    roles, permissions = _load_permissions(db, user_id)
    return roles, sorted(permissions)


def authenticate(db: Session, token: str) -> CurrentUser:
    """校验访问令牌并装配 CurrentUser（认证只需要 id/is_active）。"""
    try:
        payload = decode_access_token(token)
    except ValueError:
        raise unauthorized("AUTH_INVALID_TOKEN", "登录已失效")

    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub.isdigit():
        raise unauthorized("AUTH_INVALID_TOKEN", "登录已失效")

    user = users_repo().get_active_user_basic(db, int(sub))
    if user is None or not user.is_active:
        raise unauthorized("AUTH_INVALID_USER", "账号不可用")

    roles, permissions = _load_permissions(db, user.id)
    return CurrentUser(user=user, roles=roles, permissions=permissions)
