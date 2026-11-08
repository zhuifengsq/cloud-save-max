from __future__ import annotations

from typing import Any

from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from app.application.container import permissions_repo, roles_repo, users_repo
from app.core.permission_seed import PERMISSIONS_SEED
from app.core.security import hash_password
from app.application.auth import ensure_password_policy


def is_initialized(db: Session) -> bool:
    try:
        return users_repo().has_any_user(db)
    except (OperationalError, ProgrammingError):
        return False


def seed_permissions(db: Session) -> dict[str, Any]:
    perms_by_code: dict[str, Any] = {}
    for code, name in PERMISSIONS_SEED:
        permission = permissions_repo().get_permission_by_code(db, code)
        if permission is None:
            permission = permissions_repo().add_permission(db, code=code, name=name)
        perms_by_code[code] = permission
    return perms_by_code


def ensure_permissions_and_roles(db: Session) -> None:
    perms_by_code = seed_permissions(db)

    # Clear permissions left by older installations after removing sync tasks.
    obsolete = permissions_repo().list_permissions_by_codes(db, ("sync:read", "sync:write", "sync:run"))
    for permission in obsolete:
        permissions_repo().delete_permission(db, permission)
    db.flush()

    admin_role = roles_repo().find_role_by_name(db, "admin")
    if admin_role is None:
        admin_role = roles_repo().create_role(db, name="admin", description="系统管理员")
    admin_role.permissions = list(perms_by_code.values())


def create_initial_admin(db: Session, *, username: str, email: str, password: str) -> Any:
    ensure_password_policy(password)

    ensure_permissions_and_roles(db)
    admin_role = roles_repo().find_role_by_name(db, "admin")

    user = users_repo().create_user(db, username=username, email=email, hashed_password=hash_password(password))
    user.roles = [admin_role]
    return user