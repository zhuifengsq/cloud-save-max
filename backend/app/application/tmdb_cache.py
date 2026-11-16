"""TMDB cache use cases; persistence, HTTP and background work are injected ports."""
from __future__ import annotations
import time
from datetime import date, datetime, timedelta
from typing import Any
from sqlalchemy.orm import Session
from app.core.runtime.tx import end_read_tx
from app.application.tmdb_types import CacheRow, TMDBClientPort, WriterOptions
from app.application.tmdb_settings import DEFAULT_TMDB_SETTING_ID, get_tmdb_runtime_config
from app.application.container import tmdb_cache_store, tmdb_detail_fetcher, tmdb_refresh_background
from app.application.container import tmdb_base_language as _acc_base_language
from app.application.container import tmdb_client_cls as _acc_TMDBClient
from app.domain.tmdb import (
    MediaType, compute_expires_at, infer_tv_episode_weekdays_from_details,
    _load_json, _dump_json, _extract_summary, _normalize_cast,
    _merge_episode_with_base, _merge_tv_season_with_base, _merge_tv_details_with_base,
    _season_needs_base, _season_missing_air_dates, _fill_missing_air_dates,
)
TMDB_BASE_LANGUAGE = _acc_base_language()
TMDBClient = _acc_TMDBClient()
TMDB_SCHEDULE_LANGUAGE = "en-US"
_EPISODE_DATE_VERIFY_LIMIT = 20
_EPISODE_DATE_VERIFY_STOP_AFTER = 6
_lock_timeout = timedelta(minutes=10)
_WRITER_CONNECT_TIMEOUT_SECONDS = 2.0
_WRITE_SLOTS_TIMEOUT_SECONDS = 3.0
_CLAIM_ATTEMPTS = 2
_CLAIM_RETRY_DELAY_SECONDS = 0.2
_CREDITS_TTL = timedelta(days=7)
_CREDITS_FAIL_TTL = timedelta(minutes=10)
_CREDITS_MAX_CAST = 30


def _writer_options() -> WriterOptions:
    return WriterOptions(_WRITER_CONNECT_TIMEOUT_SECONDS, _WRITE_SLOTS_TIMEOUT_SECONDS, _CLAIM_RETRY_DELAY_SECONDS)


def _touch_last_accessed_at_best_effort(db: Session, row: CacheRow, accessed_at: datetime) -> None:
    tmdb_cache_store().touch(db, row, accessed_at, _writer_options())


def _read_cache_row(db: Session, key: dict[str, Any], *, reload: bool = False) -> CacheRow | None:
    return tmdb_cache_store().read(db, key, reload=reload)


def _claim_refresh(db: Session, key: dict[str, Any], now: datetime, *, credits: bool, force: bool = False, existing_only: bool = False) -> bool:
    return tmdb_cache_store().claim(db, key, now, credits=credits, force=force, existing_only=existing_only, options=_writer_options())


def _complete_refresh(db: Session, key: dict[str, Any], started_at: datetime, values: dict[str, Any], *, credits: bool) -> bool:
    return tmdb_cache_store().complete(db, key, started_at, values, credits=credits, options=_writer_options())


def _fetch_tmdb_detail(client: TMDBClientPort, *, media_type: MediaType, tmdb_id: int, language: str, poster_language: str) -> tuple[dict[str, Any] | None, list[int], list[int]]:
    return tmdb_detail_fetcher().fetch(client, media_type=media_type, tmdb_id=tmdb_id, language=language, poster_language=poster_language)


def infer_tv_update_weekdays(tv_id: int, details: dict[str, Any], client: TMDBClientPort) -> list[int]:
    return tmdb_detail_fetcher().infer_update_weekdays(tv_id, details, client)


def _correct_episode_air_dates(season: Any, client: TMDBClientPort, tmdb_id: int, season_number: int, *, budget: int = _EPISODE_DATE_VERIFY_LIMIT, stop_after_no_change: int = _EPISODE_DATE_VERIFY_STOP_AFTER, today: date | None = None) -> int:
    return tmdb_detail_fetcher().correct_episode_dates(season, client, tmdb_id, season_number, budget=budget, stop_after_no_change=stop_after_no_change, today=today)



class TMDBCacheBusy(RuntimeError):
    """TMDB 缓存写不进去：数据库写锁被占用，或另一条刷新正持有该条目的刷新标记。

    后台刷新/任务执行等调用方使用默认 raise_on_busy=False 保持「静默跳过、下次再刷」的旧语义；
    用户主动点刷新的路由传 raise_on_busy=True，把本异常映射成 503，避免「假成功」。
    """


def _now() -> datetime:
    return datetime.now()


def _close_read_transaction(db: Session) -> None:
    """结束调用方会话的读事务，供长批量刷新在两次外部 IO 之间让出 WAL checkpoint。

    只允许在「调用方确认当前没有未提交写入」的只读上下文里使用（只读路由、后台刷新
    任务）。SQLite WAL 的 checkpoint 需要所有读事务结束才能推进，否则 -wal 会随批量
    刷新持续增长（见方案 §7 R14）。
    """
    try:
        end_read_tx(db)
    except Exception:
        db.rollback()
        raise


def _get_runtime_tmdb_config(db: Session) -> tuple[bool, str, str, str]:
    setting = tmdb_cache_store().get_setting(db, DEFAULT_TMDB_SETTING_ID)
    cfg = get_tmdb_runtime_config(setting) if setting is not None else {}
    api_key = str(cfg.get("api_key") or "").strip()
    if not api_key:
        return False, "", "", ""
    language = str(cfg.get("language") or "zh-CN").strip() or "zh-CN"
    poster_language = str(cfg.get("poster_language") or "zh-CN").strip() or "zh-CN"
    return True, api_key, language, poster_language


def _new_tmdb_client(*, api_key: str, language: str) -> TMDBClientPort:
    return TMDBClient(api_key, language=language)


def _cache_key(media_type: MediaType, tmdb_id: int, language: str, poster_language: str) -> dict[str, Any]:
    return dict(media_type=media_type, tmdb_id=tmdb_id, language=language, poster_language=poster_language)


def _get_cache_row(db: Session, *, media_type: MediaType, tmdb_id: int, language: str, poster_language: str) -> CacheRow | None:
    return _read_cache_row(db, _cache_key(media_type, tmdb_id, language, poster_language))


def _claim_refresh_with_retry(
    db: Session, key: dict[str, Any], now: datetime, *,
    credits: bool, force: bool = False, existing_only: bool = False, attempts: int = _CLAIM_ATTEMPTS,
) -> bool:
    """领刷新标记；写锁瞬时争用时重试有限次数，仍失败返回 False（调用方决定是否报错）。"""
    for attempt in range(1, attempts + 1):
        if _claim_refresh(db, key, now, credits=credits, force=force, existing_only=existing_only):
            return True
        if attempt < attempts:
            time.sleep(_CLAIM_RETRY_DELAY_SECONDS)
            now = _now()
    return False


def _busy_message(row: CacheRow | None) -> str:
    if row is not None and bool(getattr(row, "refresh_in_progress", False)):
        return "这个条目正在刷新中（或上次刷新没有正常收尾），请稍后重试。"
    return "数据库写入被占用，刷新没有执行，请稍后重试。"


def _cached_details(
    row: CacheRow | None, media_type: MediaType,
) -> tuple[dict[str, Any] | None, list[int], list[int]]:
    if row is None:
        return None, [], []
    payload = _load_json(row.payload_json)
    stored = _load_json(row.update_weekdays_json) if row.update_weekdays_json else None
    details = payload if isinstance(payload, dict) else None
    update_weekdays = stored if isinstance(stored, list) else []
    episode_weekdays = infer_tv_episode_weekdays_from_details(details) if details is not None and media_type == "tv" else []
    return details, update_weekdays, episode_weekdays


def refresh_tmdb_detail_sync(
    db: Session,
    *,
    media_type: MediaType,
    tmdb_id: int,
    language: str,
    poster_language: str,
    force: bool = False,
    existing_only: bool = False,
    close_read_tx: bool = False,
    raise_on_busy: bool = False,
) -> tuple[CacheRow | None, dict[str, Any] | None, list[int], list[int]]:
    now = _now()
    key = _cache_key(media_type, tmdb_id, language, poster_language)
    configured, api_key, _, _ = _get_runtime_tmdb_config(db)
    if not configured:
        return _read_cache_row(db, key), None, [], []
    if close_read_tx:
        # 领标记/写回都走独立 writer 引擎；这里先结束调用方读事务，
        # 让 writer 提交时的 WAL checkpoint 不被长读事务挡住。
        _close_read_transaction(db)
    if not _claim_refresh_with_retry(db, key, now, credits=False, force=force, existing_only=existing_only):
        row = _read_cache_row(db, key, reload=True)
        if raise_on_busy:
            raise TMDBCacheBusy(_busy_message(row))
        return row, *_cached_details(row, media_type)

    try:
        client = _new_tmdb_client(api_key=api_key, language=language)
        details, update_weekdays, episode_weekdays = _fetch_tmdb_detail(
            client, media_type=media_type, tmdb_id=tmdb_id, language=language, poster_language=poster_language
        )
        completed = _now()
        values: dict[str, Any] = dict(fetched_at=completed, expires_at=compute_expires_at(details or {}, media_type=media_type, now=completed),
                      last_error=None, fail_count=0)
        if details is not None:
            values.update(_extract_summary(details, media_type=media_type))
            values.update(payload_json=_dump_json(details), update_weekdays_json=_dump_json(update_weekdays))
    except Exception as exc:
        row = _read_cache_row(db, key)
        failures = int(getattr(row, "fail_count", 0) or 0) + 1
        values = dict(fail_count=failures, last_error=str(exc),
                      expires_at=compute_expires_at({}, media_type=media_type, now=_now(), fail_count=failures))
        _complete_refresh(db, key, now, values, credits=False)
        row = _read_cache_row(db, key, reload=True)
        return row, *_cached_details(row, media_type)

    written = _complete_refresh(db, key, now, values, credits=False)
    row = _read_cache_row(db, key, reload=True)
    if not written:
        if raise_on_busy:
            raise TMDBCacheBusy("TMDB 数据已拉到，但写回缓存失败（数据库写入被占用），请稍后重试。")
        return row, *_cached_details(row, media_type)
    return row, details, update_weekdays, episode_weekdays


def trigger_refresh_async(
    db: Session, *, media_type: MediaType, tmdb_id: int, language: str, poster_language: str,
) -> bool:
    row = _get_cache_row(db, media_type=media_type, tmdb_id=tmdb_id, language=language, poster_language=poster_language)
    if row is None:
        return False
    now = _now()
    if row.refresh_in_progress and row.refresh_started_at and row.refresh_started_at > (now - _lock_timeout):
        return True
    def refresh(reader: Session) -> None:
        refresh_tmdb_detail_sync(
            reader, media_type=media_type, tmdb_id=tmdb_id, language=language,
            poster_language=poster_language, force=True, existing_only=True,
        )

    return tmdb_refresh_background().submit(db, (media_type, tmdb_id, language, poster_language), refresh)


def get_tmdb_detail_cached(
    db: Session,
    *,
    media_type: MediaType,
    tmdb_id: int,
    force_refresh: bool = False,
    close_read_tx: bool = False,
    raise_on_busy: bool = False,
) -> tuple[bool, dict[str, Any] | None, list[int], list[int], CacheRow | None]:
    configured, _, language, poster_language = _get_runtime_tmdb_config(db)
    if not configured:
        return False, None, [], [], None

    now = _now()
    row = _get_cache_row(db, media_type=media_type, tmdb_id=tmdb_id, language=language, poster_language=poster_language)
    if row is not None:
        _touch_last_accessed_at_best_effort(db, row, now)
        if force_refresh:
            row2, details, update_weekdays, episode_weekdays = refresh_tmdb_detail_sync(
                db,
                media_type=media_type,
                tmdb_id=tmdb_id,
                language=language,
                poster_language=poster_language,
                force=True,
                close_read_tx=close_read_tx,
                raise_on_busy=raise_on_busy,
            )
            return True, details, update_weekdays, episode_weekdays, row2
        if row.expires_at is not None and row.expires_at > now and row.payload_json:
            payload = _load_json(row.payload_json)
            stored = _load_json(row.update_weekdays_json) if row.update_weekdays_json else None
            update_weekdays = stored if isinstance(stored, list) else []
            episode_weekdays = (
                infer_tv_episode_weekdays_from_details(payload) if isinstance(payload, dict) and media_type == "tv" else []
            )
            return True, payload if isinstance(payload, dict) else None, update_weekdays, episode_weekdays, row
        if row.payload_json:
            trigger_refresh_async(db, media_type=media_type, tmdb_id=tmdb_id, language=language, poster_language=poster_language)
            payload = _load_json(row.payload_json)
            stored = _load_json(row.update_weekdays_json) if row.update_weekdays_json else None
            update_weekdays = stored if isinstance(stored, list) else []
            episode_weekdays = (
                infer_tv_episode_weekdays_from_details(payload) if isinstance(payload, dict) and media_type == "tv" else []
            )
            return True, payload if isinstance(payload, dict) else None, update_weekdays, episode_weekdays, row

    row2, details, update_weekdays, episode_weekdays = refresh_tmdb_detail_sync(
        db,
        media_type=media_type,
        tmdb_id=tmdb_id,
        language=language,
        poster_language=poster_language,
        force=force_refresh,
        close_read_tx=close_read_tx,
        raise_on_busy=raise_on_busy,
    )
    return True, details, update_weekdays, episode_weekdays, row2


def get_tmdb_credits_cached(
    db: Session,
    *,
    media_type: MediaType,
    tmdb_id: int,
    limit: int = 8,
    close_read_tx: bool = False,
) -> tuple[bool, list[dict[str, str]]]:
    """带 DB 缓存的 TMDB 演员表。

    credits 是完整演职员表（几百 KB），现场拉一次要 1~2 秒；演员表短期内基本不变，
    所以按「媒体 + 语言」缓存 7 天，第二次起直接读库（毫秒级，且所有任务/用户共享）。
    """
    configured, api_key, language, poster_language = _get_runtime_tmdb_config(db)
    if not configured:
        return False, []

    lim = max(1, int(limit))
    now = _now()
    key = _cache_key(media_type, tmdb_id, language, poster_language)
    row = _read_cache_row(db, key)
    cached = _normalize_cast(_load_json(row.credits_json)) if row is not None else []
    if row is not None and row.credits_expires_at is not None and row.credits_expires_at > now:
        return True, cached[:lim]
    if close_read_tx:
        _close_read_transaction(db)
    if not _claim_refresh(db, key, now, credits=True):
        row = _read_cache_row(db, key, reload=True)
        return True, _normalize_cast(_load_json(row.credits_json))[:lim] if row is not None else []

    try:
        client = _new_tmdb_client(api_key=api_key, language=language)
        raw = client.get_movie_credits(tmdb_id) if media_type == "movie" else client.get_tv_credits(tmdb_id)
        cast = _normalize_cast(raw.get("cast") if isinstance(raw, dict) else None)[:_CREDITS_MAX_CAST]
    except Exception as exc:
        row = _read_cache_row(db, key)
        values = dict(
            credits_fail_count=int(getattr(row, "credits_fail_count", 0) or 0) + 1,
            credits_last_error=str(exc), credits_expires_at=_now() + _CREDITS_FAIL_TTL,
        )
        _complete_refresh(db, key, now, values, credits=True)
        row = _read_cache_row(db, key, reload=True)
        return True, _normalize_cast(_load_json(row.credits_json))[:lim] if row is not None else []

    completed = _now()
    values = dict(credits_json=_dump_json(cast), credits_fetched_at=completed,
                  credits_expires_at=completed + _CREDITS_TTL, credits_fail_count=0, credits_last_error=None)
    if _complete_refresh(db, key, now, values, credits=True):
        return True, cast[:lim]
    row = _read_cache_row(db, key, reload=True)
    return True, _normalize_cast(_load_json(row.credits_json))[:lim] if row is not None else []


def refresh_linked_tasks(
    db: Session,
    *,
    enabled_only: bool = True,
    max_items: int = 200,
    force: bool = True,
    close_read_tx: bool = False,
) -> dict[str, int]:
    configured, _, language, poster_language = _get_runtime_tmdb_config(db)
    if not configured:
        return {"configured": 0, "targets": 0, "refreshed": 0}

    pairs = tmdb_cache_store().linked_targets(db, enabled_only=enabled_only)

    targets: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for mt, tid in pairs:
        t = str(mt or "").strip().lower()
        if t not in ("movie", "tv"):
            continue
        if tid is None:
            continue
        key = (t, int(tid))
        if key in seen:
            continue
        seen.add(key)
        targets.append(key)

    if close_read_tx:
        # targets 已是纯元组，结束读事务后逐项刷新，不让整批任务共用一个快照。
        _close_read_transaction(db)

    refreshed = 0
    for t, tid in targets[: max(0, int(max_items))]:
        row, _, _, _ = refresh_tmdb_detail_sync(
            db,
            media_type=t,  # type: ignore[arg-type]
            tmdb_id=tid,
            language=language,
            poster_language=poster_language,
            force=force,
            close_read_tx=close_read_tx,
        )
        if row is not None:
            refreshed += 1
    return {"configured": 1, "targets": len(targets), "refreshed": refreshed}


def refresh_expired_cache(
    db: Session,
    *,
    max_items: int = 200,
    force: bool = True,
    close_read_tx: bool = False,
) -> dict[str, int]:
    configured, _, language, poster_language = _get_runtime_tmdb_config(db)
    if not configured:
        return {"configured": 0, "targets": 0, "refreshed": 0}

    now = _now()
    targets = tmdb_cache_store().expired_targets(
        db, language=language, poster_language=poster_language, now=now, max_items=max_items,
    )

    if close_read_tx:
        # 行数据已取出，结束读事务：整批最多 200 次网络请求，不应共用一个读快照。
        _close_read_transaction(db)

    refreshed = 0
    for media_type, tmdb_id in targets:
        r, _, _, _ = refresh_tmdb_detail_sync(
            db,
            media_type=media_type,  # type: ignore[arg-type]
            tmdb_id=tmdb_id,
            language=language,
            poster_language=poster_language,
            force=force,
            close_read_tx=close_read_tx,
        )
        if r is not None:
            refreshed += 1
    return {"configured": 1, "targets": len(targets), "refreshed": refreshed}


def purge_cold_cache(db: Session, *, retention_days: int = 60) -> int:
    return tmdb_cache_store().purge(db, retention_days=retention_days, now=_now())


def list_cache(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 20,
    media_type: str | None = None,
    q: str | None = None,
    status: str | None = None,
    expired_only: bool = False,
) -> tuple[bool, int, str, str, list[CacheRow]]:
    configured, _, language, poster_language = _get_runtime_tmdb_config(db)
    if not configured:
        return False, 0, language, poster_language, []

    total, rows = tmdb_cache_store().list_rows(
        db, language=language, poster_language=poster_language, now=_now(),
        page=page, page_size=page_size, media_type=media_type, q=q, status=status, expired_only=expired_only,
    )
    return True, total, language, poster_language, rows


def set_ttl_seconds(db: Session, *, media_type: MediaType, tmdb_id: int, ttl_seconds: int) -> tuple[bool, CacheRow | None]:
    configured, _, language, poster_language = _get_runtime_tmdb_config(db)
    if not configured:
        return False, None
    key = _cache_key(media_type, tmdb_id, language, poster_language)
    row = tmdb_cache_store().set_ttl(db, key, _now() + timedelta(seconds=max(60, int(ttl_seconds))))
    return True, row


def delete_cache_item(db: Session, *, media_type: MediaType, tmdb_id: int) -> tuple[bool, int]:
    configured, _, language, poster_language = _get_runtime_tmdb_config(db)
    if not configured:
        return False, 0
    key = _cache_key(media_type, tmdb_id, language, poster_language)
    return True, tmdb_cache_store().delete(db, key)
