"""FastAPI 认证/权限依赖（presentation 层）。

S5：从 `app/core/deps.py` 移到 presentation——依赖注入属于表现层职责；
与框架无关的认证上下文（权限缓存、用户装载）在 `app.application.auth_context`。
"""
from __future__ import annotations

from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.application.auth_context import (
    CurrentUser,
    _load_permissions,
    authenticate,
    invalidate_permission_cache,
    load_user_roles_permissions,
)
from app.application.container import SessionLocal, get_db
from app.core.errors import forbidden

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def get_current_user(
    request: Request,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> CurrentUser:
    cached: CurrentUser | None = getattr(request.state, "current_user", None)
    if cached is not None:
        return cached
    current = authenticate(db, token)
    request.state.current_user = current
    return current


def get_current_user_scoped(
    request: Request,
    token: str = Depends(oauth2_scheme),
) -> CurrentUser:
    cached: CurrentUser | None = getattr(request.state, "current_user", None)
    if cached is not None:
        return cached
    with SessionLocal() as db:
        current = authenticate(db, token)
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
