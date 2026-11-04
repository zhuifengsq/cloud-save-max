from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session, noload

from app.core.errors import forbidden, unauthorized
from app.core.security import decode_access_token
from app.core.settings import settings
from app.db.session import SessionLocal, get_db
from app.models.permission import Permission
from app.models.role import Role
from app.models.user import User


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# 用户角色/权限查询的进程内缓存：每个已登录请求可省掉 2 条 JOIN 查询。
# 权限/角色变更时会主动失效，未覆盖到的直接改库场景最多滞后 permission_cache_ttl_seconds 秒。
_PERMISSION_CACHE_MAX_ENTRIES = 1024
_permission_cache: dict[int, tuple[float, tuple[str, ...], frozenset[str]]] = {}
_permission_cache_lock = threading.Lock()


@dataclass(frozen=True)
class CurrentUser:
    user: User
    roles: list[str]
    permissions: set[str]


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
    role_stmt = (
        select(Role.name)
        .join_from(User, User.roles)
        .where(User.id == user_id)
        .distinct()
    )
    roles = [r[0] for r in db.execute(role_stmt).all()]

    perm_stmt = (
        select(Permission.code)
        .join_from(User, User.roles)
        .join(Role.permissions)
        .where(User.id == user_id)
        .distinct()
    )
    permissions = {p[0] for p in db.execute(perm_stmt).all()}
    return roles, permissions


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


def get_current_user(
    request: Request,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> CurrentUser:
    cached: CurrentUser | None = getattr(request.state, "current_user", None)
    if cached is not None:
        return cached

    try:
        payload = decode_access_token(token)
    except ValueError:
        raise unauthorized("AUTH_INVALID_TOKEN", "登录已失效")

    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub.isdigit():
        raise unauthorized("AUTH_INVALID_TOKEN", "登录已失效")

    # 认证只需要 id/is_active，不要顺带把 roles/permissions 关系链（3 条查询）全加载出来
    user = db.get(User, int(sub), options=[noload(User.roles)])
    if user is None or not user.is_active:
        raise unauthorized("AUTH_INVALID_USER", "账号不可用")

    roles, permissions = _load_permissions(db, user.id)
    current = CurrentUser(user=user, roles=roles, permissions=permissions)
    request.state.current_user = current
    return current


def get_current_user_scoped(
    request: Request,
    token: str = Depends(oauth2_scheme),
) -> CurrentUser:
    cached: CurrentUser | None = getattr(request.state, "current_user", None)
    if cached is not None:
        return cached

    try:
        payload = decode_access_token(token)
    except ValueError:
        raise unauthorized("AUTH_INVALID_TOKEN", "登录已失效")

    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub.isdigit():
        raise unauthorized("AUTH_INVALID_TOKEN", "登录已失效")

    with SessionLocal() as db:
        user = db.get(User, int(sub), options=[noload(User.roles)])
        if user is None or not user.is_active:
            raise unauthorized("AUTH_INVALID_USER", "账号不可用")

        roles, permissions = _load_permissions(db, user.id)
        current = CurrentUser(user=user, roles=roles, permissions=permissions)
        request.state.current_user = current
        return current


def require_permissions(*codes: str):
    def _dep(current: CurrentUser = Depends(get_current_user)) -> None:
        missing = [c for c in codes if c not in current.permissions]
        if missing:
            raise forbidden("AUTH_FORBIDDEN", "权限不足", detail=",".join(missing))

    return _dep


def require_permissions_scoped(*codes: str):
    def _dep(current: CurrentUser = Depends(get_current_user_scoped)) -> None:
        missing = [c for c in codes if c not in current.permissions]
        if missing:
            raise forbidden("AUTH_FORBIDDEN", "权限不足", detail=",".join(missing))

    return _dep
