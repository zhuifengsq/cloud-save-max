"""搜索源仓储（ResourceSearchSource 的唯一 SQL 入口：内置 API 源 + 插件源共用）。"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.persistence.models.resource_search_source import ResourceSearchSource


def list_api_sources(db: Session, *, key_prefix: str) -> list[Any]:
    return (
        db.execute(
            select(ResourceSearchSource)
            .where(ResourceSearchSource.key.like(f"{key_prefix}%"))
            .order_by(ResourceSearchSource.id.desc())
        )
        .scalars()
        .all()
    )


def list_enabled_api_sources(db: Session, *, key_prefix: str) -> list[Any]:
    return (
        db.execute(
            select(ResourceSearchSource).where(
                ResourceSearchSource.key.like(f"{key_prefix}%"),
                ResourceSearchSource.enabled.is_(True),
            )
        )
        .scalars()
        .all()
    )


def get_resource_search_source(db: Session, source_id: int) -> Any:
    return db.get(ResourceSearchSource, int(source_id))


def create_resource_search_source(db: Session, *, key: str, enabled: bool, config_json: str) -> Any:
    row = ResourceSearchSource(key=key, enabled=bool(enabled), config_json=config_json)
    db.add(row)
    db.flush()
    return row


def delete_resource_search_source(db: Session, row: Any) -> None:
    db.delete(row)
    db.flush()


def ensure_plugin_rows(db: Session, keys: list[str]) -> dict[str, Any]:
    """按 key 取插件源行，缺失的补建为 enabled=False / config_json='{}'，返回 key→row 映射。"""
    if not keys:
        return {}
    rows = {
        row.key: row
        for row in db.execute(select(ResourceSearchSource).where(ResourceSearchSource.key.in_(keys))).scalars().all()
    }
    for key in keys:
        if key not in rows:
            row = ResourceSearchSource(key=key, enabled=False, config_json="{}")
            db.add(row)
            rows[key] = row
    db.flush()
    return rows
