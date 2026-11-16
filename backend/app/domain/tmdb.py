"""Pure TMDB cache, locale and schedule rules; no I/O or persistence."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any, Literal

MediaType = Literal["movie", "tv"]


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

        raw_seasons = details.get("seasons_full")
        seasons_full = raw_seasons if isinstance(raw_seasons, list) else []
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
            raw_episodes = s.get("episodes")
            episodes = raw_episodes if isinstance(raw_episodes, list) else []
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


def _season_missing_air_dates(season: Any) -> bool:
    """季记录里是否存在缺 air_date 的集（未播出的集在中文本地化记录/基础记录里常常整段为空）。"""
    if not isinstance(season, dict):
        return False
    for ep in season.get("episodes") or []:
        if isinstance(ep, dict) and not str(ep.get("air_date") or "").strip():
            return True
    return False


def _fill_missing_air_dates(season: dict[str, Any], schedule: Any) -> int:
    """用 en-US 记录的排期补齐缺失的 air_date，只填空值，不动名称/简介/其它事实字段。

    TMDB 的中文记录（乃至 language=xx 的基础记录）对未播出的集常常没有 air_date，但 en-US 记录
    带着 TMDB 网站展示的那份排期。已播出的集中文记录自己有日期，这里绝不覆盖。
    """
    if not isinstance(schedule, dict):
        return 0
    by_number: dict[int, str] = {}
    for ep in schedule.get("episodes") or []:
        if not isinstance(ep, dict):
            continue
        air = str(ep.get("air_date") or "").strip()
        if not air:
            continue
        try:
            by_number[int(ep.get("episode_number") or 0)] = air
        except (TypeError, ValueError):
            continue
    filled = 0
    for ep in season.get("episodes") or []:
        if not isinstance(ep, dict):
            continue
        if str(ep.get("air_date") or "").strip():
            continue
        try:
            number = int(ep.get("episode_number") or 0)
        except (TypeError, ValueError):
            continue
        scheduled_air = by_number.get(number)
        if scheduled_air:
            ep["air_date"] = scheduled_air
            filled += 1
    return filled


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
