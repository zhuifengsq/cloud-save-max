from __future__ import annotations

import logging
from datetime import date
from typing import Any

from app.application.dto.task import DramaUpdateProgressOut
from app.domain.tmdb import is_saved_latest_caught_up, resolve_tmdb_latest_aired_episode as _resolve_tmdb_latest_aired_episode

logger = logging.getLogger(__name__)


def resolve_tmdb_latest_aired_episode(
    details: dict[str, Any] | None, *, today: date | None = None,
) -> tuple[int | None, int | None, str | None]:
    if not isinstance(details, dict):
        return None, None, "TMDB 缓存缺失"
    season, episode, _air_date = _resolve_tmdb_latest_aired_episode(details, today=today)
    if season is None or episode is None:
        next_ep = details.get("next_episode_to_air")
        last_ep = details.get("last_episode_to_air")
        if not isinstance(next_ep, dict) and not isinstance(last_ep, dict):
            return None, None, "TMDB 缓存缺少 last/next episode"
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
    today: date | None = None,
) -> DramaUpdateProgressOut:
    """Compose progress info from TMDB's latest aired episode plus the task's saved_latest.

    ``saved_latest`` is computed during each task execution (see DramaTaskExecutor),
    so no extra drive requests are needed when rendering task lists.
    """
    tmdb_season, tmdb_episode, tmdb_reason = resolve_tmdb_latest_aired_episode(tmdb_details, today=today)
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
    if saved_season is None and int(tmdb_season) > 1:
        return DramaUpdateProgressOut(
            available=False,
            tmdb_season=tmdb_season,
            tmdb_episode=tmdb_episode,
            saved_season=saved_season,
            saved_episode=saved_episode,
            snapshot_captured_at=saved_at,
            reason="缺少已存季号，无法确认当前季进度",
        )

    behind = max(0, int(tmdb_episode) - int(saved_episode))
    caught_up = is_saved_latest_caught_up(
        tmdb_season=tmdb_season,
        tmdb_episode=tmdb_episode,
        saved_season=saved_season,
        saved_episode=saved_episode,
    )
    return DramaUpdateProgressOut(
        available=True,
        tmdb_season=tmdb_season,
        tmdb_episode=tmdb_episode,
        saved_season=saved_season,
        saved_episode=saved_episode,
        behind_episodes=behind,
        is_latest=caught_up,
        snapshot_captured_at=saved_at,
        reason=None,
    )
