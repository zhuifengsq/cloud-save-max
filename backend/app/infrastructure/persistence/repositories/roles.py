"""角色仓储。"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.role import Role


def list_roles(db: Session) -> list[Any]:
    return db.execute(select(Role).options(selectinload(Role.permissions)).order_by(Role.id.asc())).scalars().all()


def get_role(db: Session, role_id: int) -> Any:
    return db.get(Role, int(role_id))


def find_role_by_name(db: Session, name: str) -> Any:
    return db.execute(select(Role).where(Role.name == name)).scalars().first()


def role_name_exists(db: Session, name: str) -> bool:
    return db.execute(select(Role.id).where(Role.name == name)).first() is not None


def create_role(db: Session, *, name: str, description: str | None) -> Any:
    role = Role(name=name, description=description)
    db.add(role)
    db.flush()
    return role


def delete_role(db: Session, role: Any) -> None:
    db.delete(role)


def list_roles_by_ids(db: Session, role_ids: list[int]) -> list[Any]:
    return db.execute(select(Role).where(Role.id.in_(role_ids))).scalars().all()
