"""用户仓储。"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.infrastructure.persistence.models.user import User


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

def get_active_user_basic(db: Session, user_id: int) -> Any:
    """认证用：只取用户本体，不加载 roles/permissions 关系链（避免 3 条额外查询）。"""
    from sqlalchemy import select
    from sqlalchemy.orm import noload

    from app.infrastructure.persistence.models.user import User

    return (
        db.execute(select(User).options(noload(User.roles)).where(User.id == int(user_id)))
        .scalars()
        .first()
    )


def load_roles_permissions(db: Session, user_id: int) -> tuple[list[str], set[str]]:
    """用户角色名 + 权限码（两条 DISTINCT 查询，权限缓存回填用）。"""
    from sqlalchemy import select

    from app.infrastructure.persistence.models.permission import Permission
    from app.infrastructure.persistence.models.role import Role
    from app.infrastructure.persistence.models.user import User

    role_stmt = select(Role.name).join_from(User, User.roles).where(User.id == int(user_id)).distinct()
    roles = [row[0] for row in db.execute(role_stmt).all()]
    perm_stmt = (
        select(Permission.code)
        .join_from(User, User.roles)
        .join(Role.permissions)
        .where(User.id == int(user_id))
        .distinct()
    )
    permissions = {row[0] for row in db.execute(perm_stmt).all()}
    return roles, permissions
