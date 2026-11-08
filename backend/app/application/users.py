from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.application.container import roles_repo, users_repo
from app.application.auth_context import invalidate_permission_cache
from app.core.errors import bad_request, not_found
from app.core.security import hash_password
from app.application.auth import ensure_password_policy


def list_users(db: Session, *, page: int, page_size: int, q: str | None) -> "tuple[list[Any], int]":
    return users_repo().list_users(db, page=page, page_size=page_size, q=q)


def create_user(db: Session, *, username: str, email: str, password: str) -> Any:
    ensure_password_policy(password)
    if users_repo().username_or_email_exists(db, username=username, email=email):
        raise bad_request("USER_EXISTS", "用户名或邮箱已存在")
    return users_repo().create_user(db, username=username, email=email, hashed_password=hash_password(password))


def set_user_status(db: Session, *, user_id: int, is_active: bool) -> Any:
    user = users_repo().get_user(db, user_id)
    if user is None:
        raise not_found("USER_NOT_FOUND", "用户不存在")
    user.is_active = is_active
    invalidate_permission_cache(user_id)
    return user


def set_user_roles(db: Session, *, user_id: int, role_ids: list[int]) -> Any:
    user = users_repo().get_user(db, user_id)
    if user is None:
        raise not_found("USER_NOT_FOUND", "用户不存在")

    roles: list[Any] = []
    if role_ids:
        roles = roles_repo().list_roles_by_ids(db, role_ids)
        if len(roles) != len(set(role_ids)):
            raise bad_request("ROLE_NOT_FOUND", "存在无效角色")

    user.roles = roles
    invalidate_permission_cache(user_id)
    return user


def batch_set_status(db: Session, *, user_ids: list[int], is_active: bool) -> int:
    users = users_repo().list_users_by_ids(db, user_ids=user_ids)
    for u in users:
        u.is_active = is_active
        invalidate_permission_cache(u.id)
    return len(users)


def batch_set_roles(db: Session, *, user_ids: list[int], role_ids: list[int]) -> int:
    roles: list[Any] = []
    if role_ids:
        roles = roles_repo().list_roles_by_ids(db, role_ids)
        if len(roles) != len(set(role_ids)):
            raise bad_request("ROLE_NOT_FOUND", "存在无效角色")

    users = users_repo().list_users_by_ids(db, user_ids=user_ids, with_roles=True)
    for u in users:
        u.roles = roles
        invalidate_permission_cache(u.id)
    return len(users)