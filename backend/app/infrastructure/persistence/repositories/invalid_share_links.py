"""失效分享链接仓储。"""
from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.infrastructure.persistence.models.invalid_share_link import InvalidShareLink


def get_invalid_share_link(db: Session, shareurl: str) -> Any:
    return db.get(InvalidShareLink, shareurl)


def add_invalid_share_link(db: Session, *, shareurl: str) -> Any:
    item = InvalidShareLink(shareurl=shareurl)
    db.add(item)
    return item


def list_invalid_shareurls(db: Session, *, shareurls: list[str]) -> set[str]:
    rows = db.execute(select(InvalidShareLink.shareurl).where(InvalidShareLink.shareurl.in_(shareurls))).scalars().all()
    return {str(x) for x in rows if x}


def list_page(db: Session, *, page: int, page_size: int, q: str | None, drive_type: str | None) -> tuple[list[Any], int]:
    stmt = select(InvalidShareLink)
    if q:
        stmt = stmt.where(InvalidShareLink.shareurl.like(f"%{str(q).strip()}%"))
    if drive_type:
        dt = str(drive_type).strip()
        if dt:
            stmt = stmt.where(InvalidShareLink.drive_type == dt)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = (
        db.execute(
            stmt.order_by(InvalidShareLink.updated_at.desc())
            .offset((max(int(page), 1) - 1) * max(int(page_size), 1))
            .limit(max(int(page_size), 1))
        )
        .scalars()
        .all()
    )
    return rows, int(total)


def delete_by_url(db: Session, *, shareurl: str) -> int:
    res = db.execute(delete(InvalidShareLink).where(InvalidShareLink.shareurl == shareurl))
    db.flush()
    return int(getattr(res, "rowcount", 0) or 0)


def clear(db: Session, *, drive_type: str | None = None) -> int:
    stmt = delete(InvalidShareLink)
    if drive_type:
        dt = str(drive_type).strip()
        if dt:
            stmt = stmt.where(InvalidShareLink.drive_type == dt)
    res = db.execute(stmt)
    db.flush()
    return int(getattr(res, "rowcount", 0) or 0)
