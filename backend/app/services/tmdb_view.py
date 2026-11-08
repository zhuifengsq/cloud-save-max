"""TMDB 缓存视图辅助：把任务上的 tmdb_id/tmdb_media_type 映射为缓存里的状态与详情。

原先放在 app/api/routes/tasks_common.py，P5 下沉到服务层，路由只保留 HTTP 语义。
同一次请求里多个任务可能重复查同一语言配置，get_tmdb_lang_pair 已在 Session 级别去重。
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.application.container import tmdb_media_cache_repo
from app.services.tmdb_settings import get_tmdb_lang_pair


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
    """批量取剧集详情 payload（只对 drama/tv 任务有意义），解析失败记为 None。"""
    keys: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for item in items:
        if str(getattr(item, "task_type", "") or "") != "drama":
            continue
        key = tmdb_cache_key(item)
        if key is None or key in seen:
            continue
        if key[0] != "tv":
            continue
        seen.add(key)
        keys.append(key)
    if not keys:
        return {}

    language, poster_language = get_tmdb_lang_pair(db)
    tv_ids = [tid for mt, tid in keys if mt == "tv"]
    if not tv_ids:
        return {}

    rows = tmdb_media_cache_repo().load_payload_rows(
        db, language=language, poster_language=poster_language, tv_ids=tv_ids
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
