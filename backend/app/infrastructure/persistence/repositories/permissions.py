"""权限字典仓储。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.persistence.models.permission import Permission


def list_permissions(db: Session) -> list[Permission]:
    return db.execute(select(Permission).order_by(Permission.id.asc())).scalars().all()


def get_permission_by_code(db: Session, code: str) -> Permission | None:
    return db.execute(select(Permission).where(Permission.code == code)).scalars().first()


def add_permission(db: Session, *, code: str, name: str) -> Permission:
    permission = Permission(code=code, name=name)
    db.add(permission)
    return permission


def list_permissions_by_ids(db: Session, permission_ids: list[int]) -> list[Permission]:
    return db.execute(select(Permission).where(Permission.id.in_(permission_ids))).scalars().all()


def list_permissions_by_codes(db: Session, codes: tuple[str, ...]) -> list[Permission]:
    return db.execute(select(Permission).where(Permission.code.in_(codes))).scalars().all()


def delete_permission(db: Session, permission: Permission) -> None:
    permission.roles = []
    db.delete(permission)
