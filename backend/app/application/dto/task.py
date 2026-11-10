"""任务相关 DTO（application 与 presentation 共用；S5 从 app/schemas/task 迁入）。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class DramaUpdateProgressOut(BaseModel):
    available: bool = False
    tmdb_season: int | None = None
    tmdb_episode: int | None = None
    saved_season: int | None = None
    saved_episode: int | None = None
    behind_episodes: int | None = None
    is_latest: bool | None = None
    snapshot_captured_at: datetime | None = None
    reason: str | None = None


class DramaRunScheduleOut(BaseModel):
    """追剧任务「运行日期」判定结果（前端用它禁用「执行」按钮，后端用它拦截手动执行）。"""

    allowed: bool = True
    mode: str = "always"
    weekdays: list[int] = []
    reason: str = "not_drama"
    message: str = ""
