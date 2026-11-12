"""TMDB 缓存视图辅助：把任务上的 tmdb_id/tmdb_media_type 映射为缓存里的状态与详情。

原先放在 app/api/routes/tasks_common.py，P5 下沉到服务层，路由只保留 HTTP 语义。
同一次请求里多个任务可能重复查同一语言配置，get_tmdb_lang_pair 已在 Session 级别去重。
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.application.container import tmdb_media_cache_repo
from app.application.tmdb_settings import get_tmdb_lang_pair


def tmdb_cache_key(item) -> tuple[str, int] | None:
    """任务/媒体对象 → (media_type, tmdb_id)；无有效 TMDB 绑定时返回 None。"""
    tmdb_id = getattr(item, "tmdb_id", None)
    if tmdb_id is None:
        return None
    try:
        tid = int(tmdb_id)
    except Exception:
        return None
    if tid <= 0:
        return None
    mt = str(getattr(item, "tmdb_media_type", None) or "").strip().lower()
    if mt not in ("movie", "tv"):
        return None
    return mt, tid


def load_tmdb_status_map(db: Session, items: list[object]) -> dict[tuple[str, int], str | None]:
    """批量取状态（同语言、同海报语言，按 updated_at 取最新一条）。"""
    keys: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for item in items:
        key = tmdb_cache_key(item)
        if key is None or key in seen:
            continue
        seen.add(key)
        keys.append(key)
    if not keys:
        return {}

    language, poster_language = get_tmdb_lang_pair(db)
    tv_ids = [tid for mt, tid in keys if mt == "tv"]
    movie_ids = [tid for mt, tid in keys if mt == "movie"]
    if not tv_ids and not movie_ids:
        return {}

    rows = tmdb_media_cache_repo().load_status_rows(
        db, language=language, poster_language=poster_language, tv_ids=tv_ids, movie_ids=movie_ids
    )
    out: dict[tuple[str, int], str | None] = {}
    for mt, tid, status in rows:
        key = (str(mt or "").strip().lower(), int(tid))
        if key not in out:
            out[key] = str(status or "").strip() or None
    return out


def load_tmdb_payload_map(db: Session, items: list[object]) -> dict[tuple[str, int], dict[str, object] | None]:
    """批量取详情 payload（drama 任务的 tv / movie 都取），解析失败记为 None。

    movie 也取的原因：任务列表的「分类」筛选要看 genres / origin_country，
    电影任务同样需要（见 app/application/media_category.py）。
    """
    keys: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for item in items:
        if str(getattr(item, "task_type", "") or "") != "drama":
            continue
        key = tmdb_cache_key(item)
        if key is None or key in seen:
            continue
        if key[0] not in ("tv", "movie"):
            continue
        seen.add(key)
        keys.append(key)
    if not keys:
        return {}

    language, poster_language = get_tmdb_lang_pair(db)
    tv_ids = [tid for mt, tid in keys if mt == "tv"]
    movie_ids = [tid for mt, tid in keys if mt == "movie"]
    if not tv_ids and not movie_ids:
        return {}

    rows = tmdb_media_cache_repo().load_payload_rows(
        db, language=language, poster_language=poster_language, tv_ids=tv_ids, movie_ids=movie_ids
    )
    out: dict[tuple[str, int], dict[str, object] | None] = {}
    for mt, tid, payload in rows:
        key = (str(mt or "").strip().lower(), int(tid))
        if key in out:
            continue
        if not payload:
            out[key] = None
            continue
        try:
            parsed = json.loads(payload)
        except Exception:
            parsed = None
        out[key] = parsed if isinstance(parsed, dict) else None
    return out


def load_tmdb_weekday_map(
    db: Session,
    items: list[object],
    *,
    payload_map: dict[tuple[str, int], dict[str, object] | None] | None = None,
) -> dict[tuple[str, int], tuple[int, ...]]:
    """批量算「更新日」（1=周一…7=周日）：优先单集播出日，取不到回落整季更新日。

    任务列表要用它判断今天是否该跑，因此这里只读缓存、**绝不触发网络刷新**
    （refresh 走 get_tmdb_detail_cached，缓存缺失时会发请求，列表里不能用）。
    """
    from app.application.drama_schedule import normalize_weekdays
    from app.application.tmdb_cache import infer_tv_episode_weekdays_from_details

    keys: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for item in items:
        if str(getattr(item, "task_type", "") or "") != "drama":
            continue
        key = tmdb_cache_key(item)
        if key is None or key in seen or key[0] != "tv":
            continue
        seen.add(key)
        keys.append(key)
    if not keys:
        return {}

    payloads = payload_map if payload_map is not None else load_tmdb_payload_map(db, items)
    out: dict[tuple[str, int], tuple[int, ...]] = {}
    missing: list[tuple[str, int]] = []
    for key in keys:
        days = normalize_weekdays(infer_tv_episode_weekdays_from_details(payloads.get(key)))
        if days:
            out[key] = days
        else:
            missing.append(key)
    if not missing:
        return out

    language, poster_language = get_tmdb_lang_pair(db)
    rows = tmdb_media_cache_repo().load_update_weekday_rows(
        db,
        language=language,
        poster_language=poster_language,
        tv_ids=[tid for _mt, tid in missing],
    )
    fallback: dict[tuple[str, int], tuple[int, ...]] = {}
    for mt, tid, raw in rows:
        key = (str(mt or "").strip().lower(), int(tid))
        if key in fallback or not raw:
            continue
        try:
            fallback[key] = normalize_weekdays(json.loads(raw))
        except Exception:
            fallback[key] = ()
    for key in missing:
        out[key] = fallback.get(key, ())
    return out


def get_tmdb_status(db: Session, mt: str, tid: int) -> str | None:
    """单个媒体的最新状态（无批量 map 时使用）。"""
    language, poster_language = get_tmdb_lang_pair(db)
    row = tmdb_media_cache_repo().get_latest_status(
        db, media_type=mt, tmdb_id=tid, language=language, poster_language=poster_language
    )
    if row is None:
        return None
    status = str(row[0] or "").strip()
    return status or None
