from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.application.container import permissions_repo, roles_repo
from app.core.deps import invalidate_permission_cache
from app.core.errors import bad_request, not_found


def list_roles(db: Session) -> list[Any]:
    return roles_repo().list_roles(db)


def create_role(db: Session, *, name: str, description: str | None) -> Any:
    if roles_repo().role_name_exists(db, name):
        raise bad_request("ROLE_EXISTS", "角色名已存在")
    return roles_repo().create_role(db, name=name, description=description)


def update_role(db: Session, *, role_id: int, description: str | None) -> Any:
    role = roles_repo().get_role(db, role_id)
    if role is None:
        raise not_found("ROLE_NOT_FOUND", "角色不存在")
    role.description = description
    return role


def delete_role(db: Session, *, role_id: int) -> None:
    role = roles_repo().get_role(db, role_id)
    if role is None:
        raise not_found("ROLE_NOT_FOUND", "角色不存在")
    roles_repo().delete_role(db, role)
    # 角色被删除后，其成员的权限集合都会变化，直接清空整个缓存
    invalidate_permission_cache()


def set_role_permissions(db: Session, *, role_id: int, permission_ids: list[int]) -> Any:
    role = roles_repo().get_role(db, role_id)
    if role is None:
        raise not_found("ROLE_NOT_FOUND", "角色不存在")

    permissions: list[Any] = []
    if permission_ids:
        permissions = permissions_repo().list_permissions_by_ids(db, permission_ids)
        if len(permissions) != len(set(permission_ids)):
            raise bad_request("PERMISSION_NOT_FOUND", "存在无效权限")

    role.permissions = permissions
    invalidate_permission_cache()
    return role