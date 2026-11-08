"""用户仓储。"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.user import User


def list_users(db: Session, *, page: int, page_size: int, q: str | None) -> tuple[list[Any], int]:
    stmt = select(User).options(selectinload(User.roles))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(User.username.like(like), User.email.like(like)))
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    items = (
        db.execute(stmt.order_by(User.id.desc()).offset((page - 1) * page_size).limit(page_size))
        .scalars()
        .all()
    )
    return items, int(total)


def has_any_user(db: Session) -> bool:
    return db.execute(select(User.id).limit(1)).first() is not None


def get_user(db: Session, user_id: int) -> Any:
    return db.get(User, int(user_id))


def get_user_by_username(db: Session, username: str) -> Any:
    return db.execute(select(User).where(User.username == username)).scalars().first()


def username_or_email_exists(db: Session, *, username: str, email: str) -> bool:
    return db.execute(select(User.id).where(or_(User.username == username, User.email == email))).first() is not None


def create_user(db: Session, *, username: str, email: str, hashed_password: str) -> Any:
    user = User(username=username, email=email, hashed_password=hashed_password, is_active=True)
    db.add(user)
    db.flush()
    return user


def list_users_by_ids(db: Session, *, user_ids: list[int], with_roles: bool = False) -> list[Any]:
    stmt = select(User).where(User.id.in_(user_ids))
    if with_roles:
        stmt = stmt.options(selectinload(User.roles))
    return db.execute(stmt).scalars().all()
