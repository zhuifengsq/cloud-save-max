"""TMDB 媒体缓存仓储（批量状态/详情查询）。"""
from __future__ import annotations

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.infrastructure.persistence.models.tmdb_media_cache import TMDBMediaCache


def load_status_rows(db: Session, *, language: str, poster_language: str, tv_ids: list[int], movie_ids: list[int]):
    conds = []
    if tv_ids:
        conds.append(and_(TMDBMediaCache.media_type == "tv", TMDBMediaCache.tmdb_id.in_(tv_ids)))
    if movie_ids:
        conds.append(and_(TMDBMediaCache.media_type == "movie", TMDBMediaCache.tmdb_id.in_(movie_ids)))
    if not conds:
        return []
    return (
        db.execute(
            select(TMDBMediaCache.media_type, TMDBMediaCache.tmdb_id, TMDBMediaCache.status)
            .where(TMDBMediaCache.language == language, TMDBMediaCache.poster_language == poster_language, or_(*conds))
            .order_by(TMDBMediaCache.updated_at.desc())
        )
        .all()
    )


def load_payload_rows(
    db: Session,
    *,
    language: str,
    poster_language: str,
    tv_ids: list[int],
    movie_ids: list[int] | None = None,
):
    movie_ids = movie_ids or []
    conds = []
    if tv_ids:
        conds.append(and_(TMDBMediaCache.media_type == "tv", TMDBMediaCache.tmdb_id.in_(tv_ids)))
    if movie_ids:
        conds.append(and_(TMDBMediaCache.media_type == "movie", TMDBMediaCache.tmdb_id.in_(movie_ids)))
    if not conds:
        return []
    return (
        db.execute(
            select(TMDBMediaCache.media_type, TMDBMediaCache.tmdb_id, TMDBMediaCache.payload_json)
            .where(
                or_(*conds),
                TMDBMediaCache.language == language,
                TMDBMediaCache.poster_language == poster_language,
            )
            .order_by(TMDBMediaCache.updated_at.desc())
        )
        .all()
    )


def load_update_weekday_rows(db: Session, *, language: str, poster_language: str, tv_ids: list[int]):
    """批量取「整季更新日」列（轻量：不拉 payload_json），用于任务列表的运行日期判定。"""
    if not tv_ids:
        return []
    return (
        db.execute(
            select(TMDBMediaCache.media_type, TMDBMediaCache.tmdb_id, TMDBMediaCache.update_weekdays_json)
            .where(
                TMDBMediaCache.media_type == "tv",
                TMDBMediaCache.tmdb_id.in_(tv_ids),
                TMDBMediaCache.language == language,
                TMDBMediaCache.poster_language == poster_language,
            )
            .order_by(TMDBMediaCache.updated_at.desc())
        )
        .all()
    )


def get_latest_schedule_row(db: Session, *, media_type: str, tmdb_id: int, language: str, poster_language: str):
    """运行日期判定专用：只取 payload_json + update_weekdays_json（不整行加载、不触发刷新）。"""
    return (
        db.execute(
            select(TMDBMediaCache.payload_json, TMDBMediaCache.update_weekdays_json)
            .where(
                TMDBMediaCache.media_type == media_type,
                TMDBMediaCache.tmdb_id == tmdb_id,
                TMDBMediaCache.language == language,
                TMDBMediaCache.poster_language == poster_language,
            )
            .order_by(TMDBMediaCache.updated_at.desc())
            .limit(1)
        )
        .first()
    )


def get_latest_status(db: Session, *, media_type: str, tmdb_id: int, language: str, poster_language: str):
    return (
        db.execute(
            select(TMDBMediaCache.status)
            .where(
                TMDBMediaCache.media_type == media_type,
                TMDBMediaCache.tmdb_id == tmdb_id,
                TMDBMediaCache.language == language,
                TMDBMediaCache.poster_language == poster_language,
            )
            .order_by(TMDBMediaCache.updated_at.desc())
            .limit(1)
        )
        .first()
    )

def get_row(db: Session, *, media_type: str, tmdb_id: int, language: str, poster_language: str):
    """按唯一键取整行（详情/状态/刷新前探测用）。"""
    return (
        db.execute(
            select(TMDBMediaCache).where(
                TMDBMediaCache.media_type == media_type,
                TMDBMediaCache.tmdb_id == tmdb_id,
                TMDBMediaCache.language == language,
                TMDBMediaCache.poster_language == poster_language,
            )
        )
        .scalars()
        .first()
    )
