from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from threading import BoundedSemaphore, Lock
from typing import Any, Literal
from weakref import WeakKeyDictionary

from sqlalchemy import and_, create_engine, delete, exists, func, or_, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import OperationalError, TimeoutError as PoolTimeout
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.runtime.tx import end_read_tx
from app.db.session import end_read_tx
from app.models.task import Task
from app.models.tmdb_media_cache import TMDBMediaCache
from app.models.tmdb_setting import TMDBSetting
from app.application.tmdb_settings import DEFAULT_TMDB_SETTING_ID, get_tmdb_runtime_config
from app.infrastructure.http.tmdb_client import BASE_LANGUAGE as TMDB_BASE_LANGUAGE
from app.infrastructure.http.tmdb_client import TMDBClient


MediaType = Literal["movie", "tv"]

_executor = ThreadPoolExecutor(max_workers=4)
_lock_timeout = timedelta(minutes=10)
_refresh_jobs: set[tuple] = set()
_refresh_jobs_lock = Lock()
_writer_engines: WeakKeyDictionary[Engine, Engine] = WeakKeyDictionary()
_writer_engines_lock = Lock()
_write_slots = BoundedSemaphore(2)


def _now() -> datetime:
    return datetime.now()


@contextmanager
def _cache_writer(db: Session):
    bind = db.get_bind()
    engine = bind.engine if isinstance(bind, Connection) else bind
    if engine.url.database not in (None, "", ":memory:"):
        # The request may be holding a read connection during the fetch. A writer
        # must never wait for a slot in the same pool that those readers occupy.
        with _writer_engines_lock:
            writer_engine = _writer_engines.get(engine)
            if writer_engine is None:
                writer_engine = create_engine(engine.url, poolclass=NullPool, connect_args={"timeout": 0.5})
                _writer_engines[engine] = writer_engine
        bind = writer_engine

    if not _write_slots.acquire(timeout=1):
        raise PoolTimeout("TMDB cache writer busy")
    try:
        with Session(bind=bind) as writer, writer.begin():
            yield writer
    finally:
        _write_slots.release()


def _cache_write_busy(exc: Exception) -> bool:
    return isinstance(exc, PoolTimeout) or "database is locked" in str(exc).lower()


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


def _touch_last_accessed_at_best_effort(db: Session, row: TMDBMediaCache, accessed_at: datetime) -> None:
    cutoff = accessed_at - timedelta(hours=1)
    if row.last_accessed_at is not None and row.last_accessed_at >= cutoff:
        return
    try:
        with _cache_writer(db) as writer:
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


def _load_json(payload: str | None) -> Any:
    if not payload:
        return None
    try:
        return json.loads(payload)
    except Exception:
        return None


def _dump_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False)


def _pick_date(value: Any) -> str | None:
    v = str(value or "").strip()
    return v or None


def _pick_year(value: Any) -> str | None:
    d = _pick_date(value)
    if not d or len(d) < 4:
        return None
    y = d[:4]
    return y if y.isdigit() else None


def _parse_date(value: Any) -> date | None:
    try:
        ds = str(value or "").strip()
        if not ds:
            return None
        return date.fromisoformat(ds)
    except Exception:
        return None


def compute_expires_at(details: dict[str, Any], *, media_type: MediaType, now: datetime, fail_count: int = 0) -> datetime:
    if fail_count > 0:
        hours = min((2 ** max(0, fail_count - 1)), 24)
        return now + timedelta(hours=int(hours))

    if media_type == "movie":
        rd = _parse_date(details.get("release_date"))
        if rd is not None:
            if abs((rd - now.date()).days) <= 90:
                return now + timedelta(days=7)
        return now + timedelta(days=30)

    status = str(details.get("status") or "").strip()
    if status in ("Returning Series", "In Production"):
        next_ep = details.get("next_episode_to_air") if isinstance(details.get("next_episode_to_air"), dict) else None
        next_day = _parse_date(next_ep.get("air_date") if isinstance(next_ep, dict) else None)
        if next_day is not None and 0 <= (next_day - now.date()).days <= 14:
            return now + timedelta(hours=6)
        return now + timedelta(hours=12)
    if status in ("Ended", "Canceled"):
        return now + timedelta(days=30)
    return now + timedelta(hours=24)


def _infer_weekdays_from_weekday_samples(weekdays: list[int]) -> list[int]:
    samples = [int(x) for x in weekdays if 1 <= int(x) <= 7]
    if not samples:
        return []
    unique = sorted({int(x) for x in samples if 1 <= int(x) <= 7})
    total = len(samples)
    decay = 0.9
    raw_counts: dict[int, int] = {}
    weighted_counts: dict[int, float] = {}
    weighted_total = 0.0
    for i, day in enumerate(samples):
        age = (total - 1) - i
        w = float(decay**age)
        weighted_total += w
        raw_counts[int(day)] = int(raw_counts.get(int(day), 0)) + 1
        weighted_counts[int(day)] = float(weighted_counts.get(int(day), 0.0)) + w

    most_day, most_weight = max(weighted_counts.items(), key=lambda x: x[1])
    picked: list[int] = []
    if total >= 4 and weighted_total > 0 and (most_weight / weighted_total) >= 0.6:
        picked = [int(most_day)]
    else:
        for d, w in sorted(weighted_counts.items(), key=lambda x: x[1], reverse=True):
            if int(raw_counts.get(int(d), 0)) >= 2 and weighted_total > 0 and (w / weighted_total) >= 0.2:
                picked.append(int(d))
        if not picked:
            picked = list({int(x) for x in samples})

    picked = sorted({int(x) for x in picked if 1 <= int(x) <= 7})
    if total >= 7 and len(unique) >= 5 and weighted_total > 0 and (most_weight / weighted_total) <= 0.4:
        return unique
    return picked[:3]


def infer_tv_episode_weekdays_from_details(details: dict[str, Any]) -> list[int]:
    def compute(*, season_number: int | None) -> list[int]:
        out: list[date] = []
        seen: set[date] = set()

        seasons_full = details.get("seasons_full") if isinstance(details.get("seasons_full"), list) else []
        for s in seasons_full:
            if not isinstance(s, dict):
                continue
            try:
                sn = int(s.get("season_number") or 0)
            except Exception:
                sn = 0
            if sn <= 0:
                continue
            if season_number is not None and sn != int(season_number):
                continue
            episodes = s.get("episodes") if isinstance(s.get("episodes"), list) else []
            for ep in episodes:
                if not isinstance(ep, dict):
                    continue
                dt = _parse_date(ep.get("air_date"))
                if dt is None or dt in seen:
                    continue
                seen.add(dt)
                out.append(dt)

        last_ep = details.get("last_episode_to_air") if isinstance(details.get("last_episode_to_air"), dict) else None
        last_dt = _parse_date(last_ep.get("air_date") if isinstance(last_ep, dict) else None)
        if last_dt is not None and last_dt not in seen:
            seen.add(last_dt)
            out.append(last_dt)

        next_ep = details.get("next_episode_to_air") if isinstance(details.get("next_episode_to_air"), dict) else None
        next_dt = _parse_date(next_ep.get("air_date") if isinstance(next_ep, dict) else None)
        if next_dt is not None and next_dt not in seen:
            seen.add(next_dt)
            out.append(next_dt)

        out.sort()
        if not out:
            return []
        if len(out) > 100:
            out = out[-100:]
        weekdays = [d.isoweekday() for d in out]
        return _infer_weekdays_from_weekday_samples(weekdays)

    last_ep = details.get("last_episode_to_air") if isinstance(details.get("last_episode_to_air"), dict) else None
    last_season_number = int(last_ep.get("season_number") or 0) if isinstance(last_ep, dict) else 0
    if last_season_number > 0:
        picked = compute(season_number=last_season_number)
        if picked:
            return picked
    return compute(season_number=None)


def infer_tv_update_weekdays(tv_id: int, details: dict[str, Any], client: TMDBClient) -> list[int]:
    def parse_weekday(value: Any) -> int | None:
        dt = _parse_date(value)
        return dt.isoweekday() if dt is not None else None

    next_ep = details.get("next_episode_to_air") if isinstance(details.get("next_episode_to_air"), dict) else None
    next_day = parse_weekday(next_ep.get("air_date") if isinstance(next_ep, dict) else None)

    last_ep = details.get("last_episode_to_air") if isinstance(details.get("last_episode_to_air"), dict) else None
    season_number = int(last_ep.get("season_number") or 0) if isinstance(last_ep, dict) else 0
    if season_number <= 0:
        seasons = details.get("seasons") if isinstance(details.get("seasons"), list) else []
        best = 0
        for s in seasons:
            if not isinstance(s, dict):
                continue
            try:
                n = int(s.get("season_number") or 0)
            except Exception:
                continue
            if n > best:
                best = n
        season_number = best

    weekdays: list[int] = []
    if season_number > 0:
        picked = None
        seasons_full = details.get("seasons_full") if isinstance(details.get("seasons_full"), list) else []
        for s in seasons_full:
            if not isinstance(s, dict):
                continue
            try:
                sn = int(s.get("season_number") or 0)
            except Exception:
                continue
            if sn == int(season_number):
                picked = s
                break

        season = picked if isinstance(picked, dict) else (client.get_tv_season(tv_id, season_number) or {})
        episodes = season.get("episodes") if isinstance(season.get("episodes"), list) else []
        air_dates: list[str] = []
        for ep in episodes:
            if not isinstance(ep, dict):
                continue
            ad = str(ep.get("air_date") or "").strip()
            if ad:
                air_dates.append(ad)
        # 同一天多集时先去重，避免播出日被重复加权
        air_dates = sorted(set(air_dates))
        for ad in air_dates[-10:]:
            d = parse_weekday(ad)
            if d is not None:
                weekdays.append(d)

    if next_day is not None:
        weekdays.append(next_day)

    return _infer_weekdays_from_weekday_samples(weekdays)


def _extract_summary(details: dict[str, Any], *, media_type: MediaType) -> dict[str, Any]:
    if media_type == "movie":
        title = str(details.get("title") or "").strip() or None
        original = str(details.get("original_title") or "").strip() or None
        release_date = _pick_date(details.get("release_date"))
        return {
            "display_title": title,
            "original_title": original,
            "year": _pick_year(release_date),
            "status": str(details.get("status") or "").strip() or None,
            "release_date": release_date,
            "poster_path": _pick_date(details.get("poster_path")),
            "vote_average": details.get("vote_average"),
            "vote_count": details.get("vote_count"),
        }

    name = str(details.get("name") or "").strip() or None
    original_name = str(details.get("original_name") or "").strip() or None
    first_air_date = _pick_date(details.get("first_air_date"))
    last_air_date = _pick_date(details.get("last_air_date"))
    next_ep = details.get("next_episode_to_air") if isinstance(details.get("next_episode_to_air"), dict) else None
    next_episode_air_date = _pick_date(next_ep.get("air_date") if isinstance(next_ep, dict) else None)
    return {
        "display_title": name,
        "original_title": original_name,
        "year": _pick_year(first_air_date),
        "status": str(details.get("status") or "").strip() or None,
        "first_air_date": first_air_date,
        "last_air_date": last_air_date,
        "next_episode_air_date": next_episode_air_date,
        "poster_path": _pick_date(details.get("poster_path")),
        "vote_average": details.get("vote_average"),
        "vote_count": details.get("vote_count"),
    }



def _merge_episode_with_base(localized: dict[str, Any] | None, base: dict[str, Any]) -> dict[str, Any]:
    """以 TMDB 基础记录为准，文字字段（剧集名/简介）优先用本地化记录。

    翻译记录里的 air_date/runtime/still_path 常常是建条目时填的旧值，基础记录才是 TMDB 网站显示的那份。
    """
    merged = dict(base)
    if isinstance(localized, dict):
        for key in ("name", "overview"):
            value = localized.get(key)
            if isinstance(value, str) and value.strip():
                merged[key] = value
        for key, value in localized.items():
            if merged.get(key) is None and value is not None:
                merged[key] = value
    return merged


def _merge_tv_season_with_base(localized: Any, base: Any) -> Any:
    if not isinstance(base, dict) or not base.get("episodes"):
        return localized
    if not isinstance(localized, dict) or not localized.get("episodes"):
        return base
    localized_episodes: dict[int, dict[str, Any]] = {}
    for ep in localized.get("episodes") or []:
        if not isinstance(ep, dict):
            continue
        try:
            number = int(ep.get("episode_number") or 0)
        except Exception:
            continue
        localized_episodes[number] = ep
    merged_episodes: list[dict[str, Any]] = []
    seen: set[int] = set()
    for ep in base.get("episodes") or []:
        if not isinstance(ep, dict):
            continue
        try:
            number = int(ep.get("episode_number") or 0)
        except Exception:
            number = 0
        seen.add(number)
        merged_episodes.append(_merge_episode_with_base(localized_episodes.get(number), ep))
    for number, ep in localized_episodes.items():
        if number not in seen:
            merged_episodes.append(dict(ep))
    merged = dict(localized)
    merged["episodes"] = merged_episodes
    for key in ("air_date", "episode_count"):
        if base.get(key):
            merged[key] = base[key]
    return merged


def _merge_tv_details_with_base(localized: dict[str, Any], base: Any) -> dict[str, Any]:
    if not isinstance(base, dict) or not base:
        return localized
    merged = dict(localized)
    for key in (
        "first_air_date",
        "last_air_date",
        "status",
        "in_production",
        "number_of_episodes",
        "number_of_seasons",
        "episode_run_time",
        "vote_average",
        "vote_count",
    ):
        if base.get(key) is not None:
            merged[key] = base[key]
    for key in ("last_episode_to_air", "next_episode_to_air"):
        base_ep = base.get(key)
        if isinstance(base_ep, dict) and base_ep:
            merged[key] = _merge_episode_with_base(
                localized.get(key) if isinstance(localized.get(key), dict) else None, base_ep
            )
    return merged


def _season_needs_base(season: Any, *, is_latest: bool, cutoff: date) -> bool:
    """判断某一季是否需要再拉一份 TMDB 基础记录来校正事实字段。

    基础记录只在最近在播/即将播出的季里和翻译记录有差异，所以只对最新一季、以及有
    cutoff 之后（或还缺）播出日期的季额外请求，避免每季都翻倍打 TMDB。
    """
    if is_latest:
        return True
    if not isinstance(season, dict):
        return False
    for ep in season.get("episodes") or []:
        if not isinstance(ep, dict):
            continue
        air = str(ep.get("air_date") or "").strip()[:10]
        if not air:
            return True
        try:
            aired = date.fromisoformat(air)
        except ValueError:
            continue
        if aired >= cutoff:
            return True
    return False


def _get_runtime_tmdb_config(db: Session) -> tuple[bool, str, str, str]:
    with db.no_autoflush:
        setting = db.get(TMDBSetting, DEFAULT_TMDB_SETTING_ID)
    cfg = get_tmdb_runtime_config(setting) if setting is not None else {}
    api_key = str(cfg.get("api_key") or "").strip()
    if not api_key:
        return False, "", "", ""
    language = str(cfg.get("language") or "zh-CN").strip() or "zh-CN"
    poster_language = str(cfg.get("poster_language") or "zh-CN").strip() or "zh-CN"
    return True, api_key, language, poster_language


def _new_tmdb_client(*, api_key: str, language: str) -> TMDBClient:
    return TMDBClient(api_key, language=language)


def _fetch_tmdb_detail(
    client: TMDBClient,
    *,
    media_type: MediaType,
    tmdb_id: int,
    language: str,
    poster_language: str,
) -> tuple[dict[str, Any] | None, list[int], list[int]]:
    if media_type == "movie":
        details = client.get_movie_details(tmdb_id)
    else:
        details = client.get_tv_details(tmdb_id)
        if details:
            # 基础记录里的播出进度/集数比本地化记录新，事实字段以它为准
            details = _merge_tv_details_with_base(details, client.get_tv_details(tmdb_id, language=TMDB_BASE_LANGUAGE))
    if not details:
        return None, [], []

    if poster_language == "original":
        original_language = str(details.get("original_language") or "")
        if original_language == "zh":
            original_language = "zh-CN"
        if original_language and original_language != language:
            if media_type == "movie":
                alt = client.get_movie_details(tmdb_id, language=original_language)
            else:
                alt = client.get_tv_details(tmdb_id, language=original_language)
            if alt and alt.get("poster_path"):
                details["poster_path"] = alt.get("poster_path")

    update_weekdays: list[int] = []
    episode_weekdays: list[int] = []
    if media_type == "tv":
        seasons = details.get("seasons") if isinstance(details.get("seasons"), list) else []
        season_numbers: list[int] = []
        for s in seasons:
            if not isinstance(s, dict):
                continue
            try:
                sn = int(s.get("season_number"))
            except Exception:
                continue
            if sn >= 0:
                season_numbers.append(sn)
        season_numbers = sorted({int(x) for x in season_numbers})

        seasons_full: list[dict[str, Any]] = []
        failed: list[int] = []
        base_cutoff = date.today() - timedelta(days=45)
        latest_season = season_numbers[-1] if season_numbers else None
        for sn in season_numbers:
            season = client.get_tv_season(tmdb_id, int(sn))
            if _season_needs_base(season, is_latest=int(sn) == latest_season, cutoff=base_cutoff):
                base_season = client.get_tv_season(tmdb_id, int(sn), language=TMDB_BASE_LANGUAGE)
                season = _merge_tv_season_with_base(season, base_season)
            if isinstance(season, dict) and season:
                seasons_full.append(season)
            else:
                failed.append(int(sn))
        if seasons_full:
            details["seasons_full"] = seasons_full
            details["seasons_full_meta"] = {"expected": len(season_numbers), "fetched": len(seasons_full), "failed": failed[:50]}
        update_weekdays = infer_tv_update_weekdays(tmdb_id, details, client)
        episode_weekdays = infer_tv_episode_weekdays_from_details(details)
    return details, update_weekdays, episode_weekdays


def _cache_key(media_type: MediaType, tmdb_id: int, language: str, poster_language: str) -> dict[str, Any]:
    return dict(media_type=media_type, tmdb_id=tmdb_id, language=language, poster_language=poster_language)


def _read_cache_row(db: Session, key: dict[str, Any], *, reload: bool = False) -> TMDBMediaCache | None:
    with db.no_autoflush:
        return db.scalar(
            select(TMDBMediaCache).filter_by(**key).execution_options(populate_existing=reload)
        )


def _get_cache_row(db: Session, *, media_type: MediaType, tmdb_id: int, language: str, poster_language: str) -> TMDBMediaCache | None:
    return _read_cache_row(db, _cache_key(media_type, tmdb_id, language, poster_language))


def _claim_refresh(
    db: Session, key: dict[str, Any], now: datetime, *,
    credits: bool, force: bool = False, existing_only: bool = False,
) -> bool:
    expires = TMDBMediaCache.credits_expires_at if credits else TMDBMediaCache.expires_at
    started = TMDBMediaCache.credits_refresh_started_at if credits else TMDBMediaCache.refresh_started_at
    claim = update(TMDBMediaCache).filter_by(**key).where(or_(started.is_(None), started < now - _lock_timeout))
    if not force:
        expired = or_(expires.is_(None), expires <= now)
        if not credits:
            expired = or_(expired, TMDBMediaCache.payload_json.is_(None), TMDBMediaCache.payload_json == "")
        claim = claim.where(expired)
    values = {started.key: now}
    if not credits:
        values["refresh_in_progress"] = True

    try:
        with _cache_writer(db) as writer:
            if not existing_only and _read_cache_row(writer, key) is None:
                writer.execute(insert(TMDBMediaCache).values(**key).on_conflict_do_nothing(index_elements=list(key)))
            result = writer.execute(claim.values(**values))
            return result.rowcount == 1
    except (OperationalError, PoolTimeout) as exc:
        if not _cache_write_busy(exc):
            raise
        return False


def _complete_refresh(
    db: Session, key: dict[str, Any], started_at: datetime, values: dict[str, Any], *, credits: bool,
) -> bool:
    started = TMDBMediaCache.credits_refresh_started_at if credits else TMDBMediaCache.refresh_started_at
    changes = {**values, started.key: None}
    if not credits:
        changes["refresh_in_progress"] = False
    statement = update(TMDBMediaCache).filter_by(**key).where(started == started_at).values(**changes)
    for _ in range(2):
        try:
            with _cache_writer(db) as writer:
                return writer.execute(statement).rowcount == 1
        except (OperationalError, PoolTimeout) as exc:
            if not _cache_write_busy(exc):
                raise
    return False


def _cached_details(
    row: TMDBMediaCache | None, media_type: MediaType,
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
) -> tuple[TMDBMediaCache | None, dict[str, Any] | None, list[int], list[int]]:
    now = _now()
    key = _cache_key(media_type, tmdb_id, language, poster_language)
    configured, api_key, _, _ = _get_runtime_tmdb_config(db)
    if not configured:
        return _read_cache_row(db, key), None, [], []
    if close_read_tx:
        # 领标记/写回都走独立 writer 引擎；这里先结束调用方读事务，
        # 让 writer 提交时的 WAL checkpoint 不被长读事务挡住。
        _close_read_transaction(db)
    if not _claim_refresh(db, key, now, credits=False, force=force, existing_only=existing_only):
        row = _read_cache_row(db, key, reload=True)
        return row, *_cached_details(row, media_type)

    try:
        client = _new_tmdb_client(api_key=api_key, language=language)
        details, update_weekdays, episode_weekdays = _fetch_tmdb_detail(
            client, media_type=media_type, tmdb_id=tmdb_id, language=language, poster_language=poster_language
        )
        completed = _now()
        values = dict(fetched_at=completed, expires_at=compute_expires_at(details or {}, media_type=media_type, now=completed),
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
    bind = db.get_bind()
    job = (bind, media_type, tmdb_id, language, poster_language)

    def refresh():
        try:
            with Session(bind=bind) as reader:
                refresh_tmdb_detail_sync(
                    reader, media_type=media_type, tmdb_id=tmdb_id, language=language,
                    poster_language=poster_language, force=True, existing_only=True,
                )
        finally:
            with _refresh_jobs_lock:
                _refresh_jobs.discard(job)

    with _refresh_jobs_lock:
        if job in _refresh_jobs:
            return True
        if len(_refresh_jobs) >= 16:
            return False
        _refresh_jobs.add(job)
        try:
            _executor.submit(refresh)
        except RuntimeError:
            _refresh_jobs.discard(job)
            return False
    return True


def get_tmdb_detail_cached(
    db: Session,
    *,
    media_type: MediaType,
    tmdb_id: int,
    force_refresh: bool = False,
    close_read_tx: bool = False,
) -> tuple[bool, dict[str, Any] | None, list[int], list[int], TMDBMediaCache | None]:
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
    )
    return True, details, update_weekdays, episode_weekdays, row2


_CREDITS_TTL = timedelta(days=7)
_CREDITS_FAIL_TTL = timedelta(minutes=10)
_CREDITS_MAX_CAST = 30


def _normalize_cast(payload: Any) -> list[dict[str, str]]:
    if not isinstance(payload, list):
        return []
    out: list[dict[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        out.append({"name": name, "character": str(item.get("character") or "").strip()})
    return out


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

    stmt = select(Task.tmdb_media_type, Task.tmdb_id).where(Task.tmdb_id.is_not(None))
    if enabled_only:
        stmt = stmt.where(Task.enabled.is_(True))
    pairs = db.execute(stmt).all()

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
    rows = (
        db.execute(
            select(TMDBMediaCache.media_type, TMDBMediaCache.tmdb_id)
            .where(
                TMDBMediaCache.language == language,
                TMDBMediaCache.poster_language == poster_language,
                or_(TMDBMediaCache.expires_at.is_(None), TMDBMediaCache.expires_at <= now),
            )
            .order_by(TMDBMediaCache.expires_at.asc().nullsfirst())
            .limit(max(1, int(max_items)))
        )
        .all()
    )
    targets = [(str(mt), int(tid)) for mt, tid in rows if tid is not None]

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
    cutoff = _now() - timedelta(days=max(1, int(retention_days)))
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
    page: int = 1,
    page_size: int = 20,
    media_type: str | None = None,
    q: str | None = None,
    status: str | None = None,
    expired_only: bool = False,
) -> tuple[bool, int, str, str, list[TMDBMediaCache]]:
    configured, _, language, poster_language = _get_runtime_tmdb_config(db)
    if not configured:
        return False, 0, language, poster_language, []

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
        now = _now()
        stmt = stmt.where(or_(TMDBMediaCache.expires_at.is_(None), TMDBMediaCache.expires_at <= now))

    total = int(db.execute(select(func.count()).select_from(stmt.subquery())).scalar() or 0)

    rows = (
        db.execute(stmt.order_by(TMDBMediaCache.expires_at.asc().nullsfirst(), TMDBMediaCache.id.desc()).offset((p - 1) * ps).limit(ps))
        .scalars()
        .all()
    )
    return True, total, language, poster_language, rows


def set_ttl_seconds(db: Session, *, media_type: MediaType, tmdb_id: int, ttl_seconds: int) -> tuple[bool, TMDBMediaCache | None]:
    configured, _, language, poster_language = _get_runtime_tmdb_config(db)
    if not configured:
        return False, None
    row = _get_cache_row(db, media_type=media_type, tmdb_id=tmdb_id, language=language, poster_language=poster_language)
    if row is None:
        return True, None
    now = _now()
    row.expires_at = now + timedelta(seconds=max(60, int(ttl_seconds)))
    db.flush()
    return True, row


def delete_cache_item(db: Session, *, media_type: MediaType, tmdb_id: int) -> tuple[bool, int]:
    configured, _, language, poster_language = _get_runtime_tmdb_config(db)
    if not configured:
        return False, 0
    stmt = delete(TMDBMediaCache).where(
        TMDBMediaCache.media_type == media_type,
        TMDBMediaCache.tmdb_id == tmdb_id,
        TMDBMediaCache.language == language,
        TMDBMediaCache.poster_language == poster_language,
    )
    res = db.execute(stmt)
    # 演员表就存在这一行里，删行即删主演缓存
    return True, int(getattr(res, "rowcount", 0) or 0)
