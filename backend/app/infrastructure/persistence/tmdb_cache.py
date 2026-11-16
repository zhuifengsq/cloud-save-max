"""TMDB cache SQL, refresh claims and independent writer sessions."""
from __future__ import annotations
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from threading import BoundedSemaphore, Lock
from typing import Any, Iterator
from weakref import WeakKeyDictionary
from sqlalchemy import and_, create_engine, delete, exists, func, or_, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import OperationalError, TimeoutError as PoolTimeout
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from app.domain.tmdb_cache import CacheRow, WriterOptions
from app.domain.tmdb import MediaType
from app.infrastructure.persistence.models.task import Task
from app.infrastructure.persistence.models.tmdb_media_cache import TMDBMediaCache
from app.infrastructure.persistence.models.tmdb_setting import TMDBSetting

_writer_engines: WeakKeyDictionary[Engine, Engine] = WeakKeyDictionary()
_writer_engines_lock = Lock()
_write_slots = BoundedSemaphore(2)
_lock_timeout = timedelta(minutes=10)




@contextmanager
def _cache_writer(db: Session, options: WriterOptions = WriterOptions()) -> Iterator[Session]:
    bind = db.get_bind()
    engine = bind.engine if isinstance(bind, Connection) else bind
    if engine.url.database not in (None, "", ":memory:"):
        # The request may be holding a read connection during the fetch. A writer
        # must never wait for a slot in the same pool that those readers occupy.
        with _writer_engines_lock:
            writer_engine = _writer_engines.get(engine)
            if writer_engine is None:
                writer_engine = create_engine(
                    engine.url,
                    poolclass=NullPool,
                    connect_args={"timeout": options.connect_timeout},
                )
                _writer_engines[engine] = writer_engine
        bind = writer_engine

    if not _write_slots.acquire(timeout=options.slots_timeout):
        raise PoolTimeout("TMDB cache writer busy")
    try:
        with Session(bind=bind) as writer, writer.begin():
            yield writer
    finally:
        _write_slots.release()


def _cache_write_busy(exc: Exception) -> bool:
    return isinstance(exc, PoolTimeout) or "database is locked" in str(exc).lower()


def _read_cache_row(db: Session, key: dict[str, Any], *, reload: bool = False) -> TMDBMediaCache | None:
    with db.no_autoflush:
        return db.scalar(
            select(TMDBMediaCache).filter_by(**key).execution_options(populate_existing=reload)
        )


def _touch_last_accessed_at_best_effort(db: Session, row: CacheRow, accessed_at: datetime, options: WriterOptions = WriterOptions()) -> None:
    cutoff = accessed_at - timedelta(hours=1)
    if row.last_accessed_at is not None and row.last_accessed_at >= cutoff:
        return
    try:
        with _cache_writer(db, options) as writer:
            writer.execute(
                update(TMDBMediaCache)
                .where(
                    TMDBMediaCache.id == row.id,
                    or_(TMDBMediaCache.last_accessed_at.is_(None), TMDBMediaCache.last_accessed_at < cutoff),
                )
                .values(last_accessed_at=accessed_at)
            )
    except (OperationalError, PoolTimeout) as exc:
        if not _cache_write_busy(exc):
            raise


def _claim_refresh(
    db: Session, key: dict[str, Any], now: datetime, *,
    credits: bool, force: bool = False, existing_only: bool = False, options: WriterOptions = WriterOptions(),
) -> bool:
    expires = TMDBMediaCache.credits_expires_at if credits else TMDBMediaCache.expires_at
    started = TMDBMediaCache.credits_refresh_started_at if credits else TMDBMediaCache.refresh_started_at
    claim = update(TMDBMediaCache).filter_by(**key).where(or_(started.is_(None), started < now - _lock_timeout))
    if not force:
        expired = or_(expires.is_(None), expires <= now)
        if not credits:
            expired = or_(expired, TMDBMediaCache.payload_json.is_(None), TMDBMediaCache.payload_json == "")
        claim = claim.where(expired)
    values: dict[str, Any] = {started.key: now}
    if not credits:
        values["refresh_in_progress"] = True

    try:
        with _cache_writer(db, options) as writer:
            if not existing_only and _read_cache_row(writer, key) is None:
                writer.execute(insert(TMDBMediaCache).values(**key).on_conflict_do_nothing(index_elements=list(key)))
            result = writer.execute(claim.values(**values))
            return result.rowcount == 1
    except (OperationalError, PoolTimeout) as exc:
        if not _cache_write_busy(exc):
            raise
        return False


def _complete_refresh(
    db: Session, key: dict[str, Any], started_at: datetime, values: dict[str, Any], *, credits: bool, options: WriterOptions = WriterOptions(),
) -> bool:
    started = TMDBMediaCache.credits_refresh_started_at if credits else TMDBMediaCache.refresh_started_at
    changes = {**values, started.key: None}
    if not credits:
        changes["refresh_in_progress"] = False
    statement = update(TMDBMediaCache).filter_by(**key).where(started == started_at).values(**changes)
    for _ in range(2):
        try:
            with _cache_writer(db, options) as writer:
                return writer.execute(statement).rowcount == 1
        except (OperationalError, PoolTimeout) as exc:
            if not _cache_write_busy(exc):
                raise
        time.sleep(options.retry_delay)
    return False


def get_setting(db: Session, setting_id: int) -> TMDBSetting | None:
    with db.no_autoflush:
        return db.get(TMDBSetting, setting_id)


def linked_targets(db: Session, *, enabled_only: bool) -> list[tuple[str, int]]:
    stmt = select(Task.tmdb_media_type, Task.tmdb_id).where(Task.tmdb_id.is_not(None))
    if enabled_only:
        stmt = stmt.where(Task.enabled.is_(True))
    return [(str(mt or "").strip().lower(), int(tid)) for mt, tid in db.execute(stmt).all() if tid is not None]


def expired_targets(db: Session, *, language: str, poster_language: str, now: datetime, max_items: int) -> list[tuple[str, int]]:
    rows = db.execute(select(TMDBMediaCache.media_type, TMDBMediaCache.tmdb_id).where(
        TMDBMediaCache.language == language, TMDBMediaCache.poster_language == poster_language,
        or_(TMDBMediaCache.expires_at.is_(None), TMDBMediaCache.expires_at <= now),
    ).order_by(TMDBMediaCache.expires_at.asc().nullsfirst()).limit(max(1, int(max_items)))).all()
    return [(str(mt), int(tid)) for mt, tid in rows if tid is not None]



def purge_cold_cache(db: Session, *, retention_days: int = 60, now: datetime) -> int:
    cutoff = now - timedelta(days=max(1, int(retention_days)))
    referenced = exists(
        select(1).where(and_(Task.tmdb_id == TMDBMediaCache.tmdb_id, Task.tmdb_media_type == TMDBMediaCache.media_type))
    )
    stmt = delete(TMDBMediaCache).where(
        or_(
            and_(TMDBMediaCache.last_accessed_at.is_not(None), TMDBMediaCache.last_accessed_at < cutoff),
            # 从未被访问过的孤儿缓存（任务删了、换过语言留下的行）也要能清掉，
            # 否则 last_accessed_at 永远是 NULL，它们会一直占着 payload。
            and_(
                TMDBMediaCache.expires_at.is_not(None),
                TMDBMediaCache.expires_at < cutoff,
                TMDBMediaCache.fetched_at.is_not(None),
                TMDBMediaCache.fetched_at < cutoff,
            ),
        ),
        ~referenced,
    )
    res = db.execute(stmt)
    # 演员表与详情同一行：行没被删（仍被引用/最近访问过）时，把过期的演员表数据一并清掉
    db.execute(
        update(TMDBMediaCache)
        .where(
            TMDBMediaCache.credits_expires_at.is_not(None),
            TMDBMediaCache.credits_expires_at < cutoff,
        )
        .values(
            credits_json=None,
            credits_fetched_at=None,
            credits_expires_at=None,
            credits_fail_count=0,
            credits_last_error=None,
            credits_refresh_started_at=None,
        )
    )
    return int(getattr(res, "rowcount", 0) or 0)


def list_cache(
    db: Session,
    *,
    language: str,
    poster_language: str,
    now: datetime,
    page: int = 1,
    page_size: int = 20,
    media_type: str | None = None,
    q: str | None = None,
    status: str | None = None,
    expired_only: bool = False,
) -> tuple[int, list[CacheRow]]:
    p = max(1, int(page))
    ps = max(1, min(200, int(page_size)))
    stmt = select(TMDBMediaCache).where(TMDBMediaCache.language == language, TMDBMediaCache.poster_language == poster_language)

    mt = str(media_type or "").strip().lower()
    if mt in ("movie", "tv"):
        stmt = stmt.where(TMDBMediaCache.media_type == mt)

    s = str(status or "").strip()
    if s:
        stmt = stmt.where(TMDBMediaCache.status == s)

    keyword = str(q or "").strip()
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(or_(TMDBMediaCache.display_title.ilike(like), TMDBMediaCache.original_title.ilike(like)))

    if expired_only:
        stmt = stmt.where(or_(TMDBMediaCache.expires_at.is_(None), TMDBMediaCache.expires_at <= now))

    total = int(db.execute(select(func.count()).select_from(stmt.subquery())).scalar() or 0)

    rows = (
        db.execute(stmt.order_by(TMDBMediaCache.expires_at.asc().nullsfirst(), TMDBMediaCache.id.desc()).offset((p - 1) * ps).limit(ps))
        .scalars()
        .all()
    )
    return total, list(rows)


def set_ttl(db: Session, key: dict[str, Any], expires_at: datetime) -> TMDBMediaCache | None:
    row = _read_cache_row(db, key)
    if row is not None:
        row.expires_at = expires_at
        db.flush()
    return row


def delete_item(db: Session, key: dict[str, Any]) -> int:
    result = db.execute(delete(TMDBMediaCache).filter_by(**key))
    return int(getattr(result, "rowcount", 0) or 0)


class TMDBCacheStore:
    read = staticmethod(_read_cache_row)
    get_setting = staticmethod(get_setting)
    touch = staticmethod(_touch_last_accessed_at_best_effort)
    claim = staticmethod(_claim_refresh)
    complete = staticmethod(_complete_refresh)
    linked_targets = staticmethod(linked_targets)
    expired_targets = staticmethod(expired_targets)
    purge = staticmethod(purge_cold_cache)
    list_rows = staticmethod(list_cache)
    set_ttl = staticmethod(set_ttl)
    delete = staticmethod(delete_item)

