from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from app.schemas.task import DramaUpdateProgressOut

logger = logging.getLogger(__name__)


def _parse_air_date(value: Any) -> date | None:
    try:
        s = str(value or "").strip()
        if not s:
            return None
        return date.fromisoformat(s)
    except Exception:
        return None


def resolve_tmdb_latest_aired_episode(details: dict[str, Any] | None) -> tuple[int | None, int | None, str | None]:
    if not isinstance(details, dict):
        return None, None, "TMDB 缓存缺失"

    next_ep = details.get("next_episode_to_air") if isinstance(details.get("next_episode_to_air"), dict) else None
    last_ep = details.get("last_episode_to_air") if isinstance(details.get("last_episode_to_air"), dict) else None

    picked = None
    if isinstance(next_ep, dict):
        ad = _parse_air_date(next_ep.get("air_date"))
        if ad is not None and ad <= datetime.now().date():
            picked = next_ep
    if picked is None:
        picked = last_ep if isinstance(last_ep, dict) else None
    if not isinstance(picked, dict):
        return None, None, "TMDB 缓存缺少 last/next episode"

    try:
        season = int(picked.get("season_number") or 0)
        episode = int(picked.get("episode_number") or 0)
    except Exception:
        return None, None, "TMDB last/next episode 数据异常"
    if season <= 0 or episode <= 0:
        return None, None, "TMDB last/next episode 数据缺失"
    return season, episode, None


def _to_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def build_drama_update_progress(
    *,
    tmdb_details: dict[str, Any] | None,
    saved_latest: dict[str, Any] | None,
) -> DramaUpdateProgressOut:
    """Compose progress info from TMDB's latest aired episode plus the task's saved_latest.

    ``saved_latest`` is computed during each task execution (see DramaTaskExecutor),
    so no extra drive requests are needed when rendering task lists.
    """
    tmdb_season, tmdb_episode, tmdb_reason = resolve_tmdb_latest_aired_episode(tmdb_details)
    saved = saved_latest if isinstance(saved_latest, dict) else {}
    saved_season = _to_int(saved.get("season"))
    saved_episode = _to_int(saved.get("episode"))
    saved_at = str(saved.get("at") or "").strip() or None

    if tmdb_season is None or tmdb_episode is None:
        return DramaUpdateProgressOut(
            available=saved_episode is not None,
            saved_season=saved_season,
            saved_episode=saved_episode,
            snapshot_captured_at=saved_at,
            reason=tmdb_reason or "无TMDB信息",
        )

    if saved_episode is None:
        return DramaUpdateProgressOut(
            available=False,
            tmdb_season=tmdb_season,
            tmdb_episode=tmdb_episode,
            snapshot_captured_at=saved_at,
            reason="暂无已存进度",
        )

    if saved_season is not None and int(saved_season) != int(tmdb_season):
        return DramaUpdateProgressOut(
            available=False,
            tmdb_season=tmdb_season,
            tmdb_episode=tmdb_episode,
            saved_season=saved_season,
            saved_episode=saved_episode,
            snapshot_captured_at=saved_at,
            reason="当前季无匹配文件",
        )

    behind = max(0, int(tmdb_episode) - int(saved_episode))
    return DramaUpdateProgressOut(
        available=True,
        tmdb_season=tmdb_season,
        tmdb_episode=tmdb_episode,
        saved_season=saved_season,
        saved_episode=saved_episode,
        behind_episodes=behind,
        is_latest=behind == 0,
        snapshot_captured_at=saved_at,
        reason=None,
    )
