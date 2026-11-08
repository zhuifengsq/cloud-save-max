"""分享预览批量缓存仓储。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.share_preview_batch_cache import SharePreviewBatchCache


def _utcnow() -> datetime:
    return datetime.now()


def get_cached_item(db: Session, *, shareurl: str):
    return db.get(SharePreviewBatchCache, shareurl)


def new_item(db: Session, *, shareurl: str, expires_at: datetime):
    row = SharePreviewBatchCache(shareurl=shareurl, expires_at=expires_at)
    db.add(row)
    return row


def delete_expired_before(db: Session, *, threshold: datetime) -> int:
    res = db.execute(delete(SharePreviewBatchCache).where(SharePreviewBatchCache.expires_at < threshold))
    return int(getattr(res, "rowcount", 0) or 0)


def list_items(db: Session, *, page: int, page_size: int, q: str | None, drive_type: str | None, ok: bool | None, expired_only: bool):
    stmt = select(SharePreviewBatchCache)
    if q:
        like = f"%{str(q).strip()}%"
        stmt = stmt.where(SharePreviewBatchCache.shareurl.like(like))
    if drive_type:
        dt = str(drive_type).strip()
        if dt:
            stmt = stmt.where(SharePreviewBatchCache.drive_type == dt)
    if ok is not None:
        stmt = stmt.where(SharePreviewBatchCache.ok.is_(bool(ok)))
    if expired_only:
        stmt = stmt.where(SharePreviewBatchCache.expires_at <= _utcnow())

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = (
        db.execute(
            stmt.order_by(SharePreviewBatchCache.checked_at.desc())
            .offset((max(int(page), 1) - 1) * max(int(page_size), 1))
            .limit(max(int(page_size), 1))
        )
        .scalars()
        .all()
    )
    return rows, int(total)


def delete_by_shareurl(db: Session, *, shareurl: str) -> int:
    res = db.execute(delete(SharePreviewBatchCache).where(SharePreviewBatchCache.shareurl == shareurl))
    return int(getattr(res, "rowcount", 0) or 0)


def delete_expired(db: Session, *, threshold: datetime) -> int:
    res = db.execute(delete(SharePreviewBatchCache).where(SharePreviewBatchCache.expires_at < threshold))
    return int(getattr(res, "rowcount", 0) or 0)