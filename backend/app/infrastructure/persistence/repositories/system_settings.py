"""系统设置键值仓储（system_settings 表；S5 从路由查询下沉）。"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.persistence.models.system_setting import SystemSetting


def get_row(db: Session, key: str) -> Any:
    return db.execute(select(SystemSetting).where(SystemSetting.key == key)).scalars().first()


def get_value(db: Session, key: str, default: str = "") -> str:
    row = get_row(db, key)
    return row.value if row is not None else default


def list_values(db: Session, keys: list[str]) -> dict[str, str]:
    if not keys:
        return {}
    rows = db.execute(select(SystemSetting).where(SystemSetting.key.in_(keys))).scalars().all()
    return {row.key: row.value for row in rows}


def set_value(db: Session, key: str, value: str, description: str = "") -> None:
    row = get_row(db, key)
    if row is not None:
        row.value = value
        if description:
            row.description = description
    else:
        db.add(SystemSetting(key=key, value=value, description=description))
