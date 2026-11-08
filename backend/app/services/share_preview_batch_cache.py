from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.application.container import share_preview_batch_cache_repo


def _utcnow() -> datetime:
    return datetime.now()


def get_cached_preview_batch_item(db: Session, *, shareurl: str) -> "tuple[Any | None, bool]":
    url = str(shareurl or "").strip()
    if not url:
        return (None, False)

    row = share_preview_batch_cache_repo().get_cached_item(db, shareurl=url)
    if row is None:
        return (None, False)

    now = _utcnow()
    expires = row.expires_at
    if expires is None or expires <= now:
        return (None, False)

    row.hit_count = int(getattr(row, "hit_count", 0) or 0) + 1
    db.flush()
    return (row, True)


def upsert_preview_batch_cache(
    db: Session,
    *,
    shareurl: str,
    drive_type: str | None,
    ok: bool,
    message: str | None,
    ttl_seconds: int,
) -> bool:
    url = str(shareurl or "").strip()
    if not url:
        return False

    ttl = max(1, int(ttl_seconds or 0))
    now = _utcnow()
    expires_at = now + timedelta(seconds=ttl)

    row = share_preview_batch_cache_repo().get_cached_item(db, shareurl=url)
    if row is None:
        row = share_preview_batch_cache_repo().new_item(db, shareurl=url, expires_at=expires_at)

    row.drive_type = str(drive_type or "").strip() or None
    row.ok = bool(ok)
    row.message = str(message or "").strip() or None
    row.checked_at = now
    row.expires_at = expires_at
    db.flush()
    return True


def purge_old_preview_batch_cache(db: Session, *, retention_seconds: int) -> int:
    keep = max(1, int(retention_seconds or 0))
    threshold = _utcnow() - timedelta(seconds=keep)
    deleted = share_preview_batch_cache_repo().delete_expired_before(db, threshold=threshold)
    db.flush()
    return deleted


def list_preview_batch_cache(
    db: Session,
    *,
    page: int,
    page_size: int,
    q: str | None,
    drive_type: str | None,
    ok: bool | None,
    expired_only: bool,
) -> "tuple[list[Any], int]":
    return share_preview_batch_cache_repo().list_items(
        db, page=page, page_size=page_size, q=q, drive_type=drive_type, ok=ok, expired_only=expired_only
    )


def delete_preview_batch_cache_item(db: Session, *, shareurl: str) -> int:
    url = str(shareurl or "").strip()
    if not url:
        return 0
    deleted = share_preview_batch_cache_repo().delete_by_shareurl(db, shareurl=url)
    db.flush()
    return deleted


def purge_expired_preview_batch_cache(db: Session) -> int:
    threshold = _utcnow()
    deleted = share_preview_batch_cache_repo().delete_expired(db, threshold=threshold)
    db.flush()
    return deleted