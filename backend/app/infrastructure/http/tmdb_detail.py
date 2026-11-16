"""TMDB multi-request detail fetching and bounded episode date verification."""
from __future__ import annotations
from datetime import date, timedelta
from typing import Any
from app.domain.tmdb import (
    MediaType, _parse_date, _infer_weekdays_from_weekday_samples,
    infer_tv_episode_weekdays_from_details, _merge_tv_details_with_base,
    _merge_tv_season_with_base, _season_needs_base, _season_missing_air_dates,
    _fill_missing_air_dates,
)
from app.application.tmdb_types import TMDBClientPort
from app.infrastructure.http.tmdb_client import BASE_LANGUAGE as TMDB_BASE_LANGUAGE
TMDB_SCHEDULE_LANGUAGE = "en-US"
_EPISODE_DATE_VERIFY_LIMIT = 20
_EPISODE_DATE_VERIFY_WINDOW_DAYS = 45
_EPISODE_DATE_VERIFY_STOP_AFTER = 6

def infer_tv_update_weekdays(tv_id: int, details: dict[str, Any], client: TMDBClientPort) -> list[int]:
    def parse_weekday(value: Any) -> int | None:
        dt = _parse_date(value)
        return dt.isoweekday() if dt is not None else None

    next_ep = details.get("next_episode_to_air") if isinstance(details.get("next_episode_to_air"), dict) else None
    next_day = parse_weekday(next_ep.get("air_date") if isinstance(next_ep, dict) else None)

    last_ep = details.get("last_episode_to_air") if isinstance(details.get("last_episode_to_air"), dict) else None
    season_number = int(last_ep.get("season_number") or 0) if isinstance(last_ep, dict) else 0
    if season_number <= 0:
        raw_seasons = details.get("seasons")
        seasons = raw_seasons if isinstance(raw_seasons, list) else []
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
        raw_full = details.get("seasons_full")
        seasons_full = raw_full if isinstance(raw_full, list) else []
        for s in seasons_full:
            if not isinstance(s, dict):
                continue
            try:
                raw_number = s.get("season_number")
                if raw_number is None:
                    continue
                sn = int(raw_number)
            except Exception:
                continue
            if sn == int(season_number):
                picked = s
                break

        season = picked if isinstance(picked, dict) else (client.get_tv_season(tv_id, season_number) or {})
        raw_episodes = season.get("episodes")
        episodes = raw_episodes if isinstance(raw_episodes, list) else []
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


def _correct_episode_air_dates(
    season: Any,
    client: TMDBClientPort,
    tmdb_id: int,
    season_number: int,
    *,
    budget: int = _EPISODE_DATE_VERIFY_LIMIT,
    stop_after_no_change: int = _EPISODE_DATE_VERIFY_STOP_AFTER,
    today: date | None = None,
) -> int:
    """用单集记录校正季列表里错误的 air_date，返回被改正的集数。

    TMDB 的季列表记录对某些剧会整季停在建条目时的旧排期（例：「今天是太阳雨」季列表把第 9 集
    记成 11-10，而 TMDB 网站显示 11-09）；网站集卡片按单集记录
    (/tv/{id}/season/{n}/episode/{m}) 渲染，所以单集记录才是事实源。

    为了不把整季翻一遍：只校正「缺日期」或「播出日期落在今天 ±45 天内」的集，按距今天数由近到远
    取前 budget 个；已播很久的季没有候选集，一次请求都不发。单集请求失败只跳过该集，不影响整次刷新。
    连续 stop_after_no_change 集都和季列表一致时提前收手：季列表没跑偏的剧不用把整季翻一遍。
    """
    if not isinstance(season, dict):
        return 0
    today = today or date.today()
    candidates: list[tuple[int, int, dict[str, Any], str]] = []
    for ep in season.get("episodes") or []:
        if not isinstance(ep, dict):
            continue
        try:
            number = int(ep.get("episode_number") or 0)
        except (TypeError, ValueError):
            continue
        if number <= 0:
            continue
        air = str(ep.get("air_date") or "").strip()[:10]
        distance = 0
        if air:
            try:
                aired: date | None = date.fromisoformat(air)
            except ValueError:
                aired = None
            if aired is not None:
                distance = abs((aired - today).days)
                if distance > _EPISODE_DATE_VERIFY_WINDOW_DAYS:
                    continue
        candidates.append((distance, number, ep, air))
    if not candidates:
        return 0
    candidates.sort(key=lambda item: (item[0], item[1]))
    corrected = 0
    unchanged_streak = 0
    for _distance, number, ep, listed in candidates[: max(0, int(budget))]:
        try:
            detail = client.get_tv_episode(int(tmdb_id), int(season_number), number, language=TMDB_BASE_LANGUAGE)
        except Exception:
            continue
        if not isinstance(detail, dict):
            continue
        actual = str(detail.get("air_date") or "").strip()[:10]
        if not actual or actual == listed:
            unchanged_streak += 1
            if stop_after_no_change > 0 and unchanged_streak >= stop_after_no_change:
                break
            continue
        ep["air_date"] = actual
        corrected += 1
        unchanged_streak = 0
    return corrected


def _fetch_tmdb_detail(
    client: TMDBClientPort,
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
        raw_seasons = details.get("seasons")
        seasons = raw_seasons if isinstance(raw_seasons, list) else []
        season_numbers: list[int] = []
        for s in seasons:
            if not isinstance(s, dict):
                continue
            try:
                raw_number = s.get("season_number")
                if raw_number is None:
                    continue
                sn = int(raw_number)
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
                # 中文/基础记录里未播出的集没有 air_date，en-US 记录才有排期；只补空值
                if _season_missing_air_dates(season):
                    schedule = client.get_tv_season(tmdb_id, int(sn), language=TMDB_SCHEDULE_LANGUAGE)
                    _fill_missing_air_dates(season, schedule)
                # 季列表可能整季停在旧排期（网站按单集记录渲染），近窗口内的集用单集记录校正
                if isinstance(season, dict) and season.get("episodes"):
                    _correct_episode_air_dates(season, client, tmdb_id, int(sn))
                seasons_full.append(season)
            else:
                failed.append(int(sn))
        if seasons_full:
            details["seasons_full"] = seasons_full
            details["seasons_full_meta"] = {"expected": len(season_numbers), "fetched": len(seasons_full), "failed": failed[:50]}
        update_weekdays = infer_tv_update_weekdays(tmdb_id, details, client)
        episode_weekdays = infer_tv_episode_weekdays_from_details(details)
    return details, update_weekdays, episode_weekdays


class TMDBDetailFetcher:
    fetch = staticmethod(_fetch_tmdb_detail)
    correct_episode_dates = staticmethod(_correct_episode_air_dates)
    infer_update_weekdays = staticmethod(infer_tv_update_weekdays)
