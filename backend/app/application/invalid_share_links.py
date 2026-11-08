from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.application.container import invalid_share_links_repo


def upsert_invalid_share_link(
    db: Session,
    *,
    shareurl: str,
    drive_type: str | None = None,
    message: str | None = None,
) -> bool:
    url = str(shareurl or "").strip()
    if not url:
        return False

    repo = invalid_share_links_repo()
    item = repo.get_invalid_share_link(db, url)
    if item is None:
        item = repo.add_invalid_share_link(db, shareurl=url)

    item.drive_type = str(drive_type or "").strip() or None
    item.message = str(message or "").strip() or None
    item.hit_count = int(getattr(item, "hit_count", 0) or 0) + 1
    db.flush()
    return True


def list_invalid_shareurls(db: Session, *, shareurls: list[str]) -> set[str]:
    urls = [str(x or "").strip() for x in (shareurls or [])]
    urls = [x for x in urls if x]
    if not urls:
        return set()
    return invalid_share_links_repo().list_invalid_shareurls(db, shareurls=urls)


def list_invalid_share_links(
    db: Session,
    *,
    page: int,
    page_size: int,
    q: str | None,
    drive_type: str | None,
) -> "tuple[list[Any], int]":
    return invalid_share_links_repo().list_page(db, page=page, page_size=page_size, q=q, drive_type=drive_type)


def delete_invalid_share_link(db: Session, *, shareurl: str) -> int:
    url = str(shareurl or "").strip()
    if not url:
        return 0
    return invalid_share_links_repo().delete_by_url(db, shareurl=url)


def clear_invalid_share_links(db: Session, *, drive_type: str | None = None) -> int:
    return invalid_share_links_repo().clear(db, drive_type=drive_type)
