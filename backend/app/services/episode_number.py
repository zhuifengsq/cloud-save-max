from __future__ import annotations

import re
from typing import Any

_RE_SEASON_EPISODE = re.compile(r"\bS(\d{1,3})E(\d{1,4})\b", re.IGNORECASE)
_RE_EPISODE_ONLY = re.compile(r"\b(?:E(?:P(?:ISODE)?)?|第)\s*0*(\d{1,4})\s*(?:集)?\b", re.IGNORECASE)
_RE_EPISODE_CN = re.compile(r"第\s*0*(\d{1,4})\s*[集期话部篇]")
_RE_EPISODE_E = re.compile(r"(?<![A-Za-z])[Ee][Pp]?\.?\s*0*(\d{1,4})(?!\d)")
_RE_TRAILING_NUMBER = re.compile(r"^0*(\d{1,4})(?!\d)(?![pPiIkK])")

# 日期（综艺常见 2026.1.13 / 2026-01-13 / 2026年1月13日 / 20260113）
_RE_DATE_WITH_DAY = re.compile(
    r"(?<!\d)(?:19|20)\d{2}\s*(?:[.\-/年])\s*\d{1,2}\s*(?:[.\-/月])\s*\d{1,2}\s*日?(?!\d)"
)
_RE_DATE_COMPACT = re.compile(r"(?<!\d)(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])(?!\d)")
_RE_DATE_MONTH_ONLY = re.compile(r"(?<!\d)(?:19|20)\d{2}\s*年\s*\d{1,2}\s*月(?!\d)")

# 清晰度/画幅数字不应被当成集数
_NON_EPISODE_NUMBERS = {360, 480, 540, 576, 720, 1080, 1440, 2160, 4320}

# 分享者常把"更新到第几集"写进文件夹名，例如 "4K SDR 低码 26集"
_RE_FOLDER_EPISODE = re.compile(r"(?:更新?至|全|共|第)?\s*0*(\d{1,4})\s*集")

_SIZE_UNITS = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}


def extract_episode(file_name: str, *, mr: Any = None) -> tuple[int | None, int | None]:
    """Extract (season, episode) after applying the task rename rule.

    Without a rename rule the file name is not normalized, so episode numbers are
    only recognized when the name already looks like SxxExx / E05 / 第5集.
    """
    text = str(file_name or "").strip()
    if not text:
        return None, None
    if mr is not None:
        resolved_pattern = str(getattr(mr, "_resolved_pattern", "") or "").strip()
        resolved_replace = str(getattr(mr, "_resolved_replace", "") or "").strip()
        if resolved_pattern or resolved_replace:
            text = mr.sub(resolved_pattern, resolved_replace, text)
    if match := _RE_SEASON_EPISODE.search(text):
        return int(match.group(1)), int(match.group(2))
    if match := _RE_EPISODE_ONLY.search(text):
        return None, int(match.group(1))
    return None, None


def _strip_date_tokens(text: str) -> str:
    """去掉文件名里的日期，避免 "2026.1.13" 被当成第 2026 集。"""
    cleaned = _RE_DATE_WITH_DAY.sub(" ", text)
    cleaned = _RE_DATE_COMPACT.sub(" ", cleaned)
    cleaned = _RE_DATE_MONTH_ONLY.sub(" ", cleaned)
    return cleaned


def _is_year_like(number: int) -> bool:
    return 1900 <= number <= 2100


def guess_episode(file_name: str) -> int | None:
    """Best-effort episode number for names that were not renamed."""
    text = str(file_name or "").strip()
    if not text:
        return None
    if match := _RE_SEASON_EPISODE.search(text):
        return int(match.group(2))
    text = _strip_date_tokens(text)
    for pattern in (_RE_EPISODE_CN, _RE_EPISODE_E, _RE_TRAILING_NUMBER):
        if match := pattern.search(text):
            try:
                number = int(match.group(1))
            except (TypeError, ValueError):
                continue
            if number in _NON_EPISODE_NUMBERS or _is_year_like(number):
                continue
            return number
    return None


def episode_from_folder_name(folder_name: str) -> int | None:
    """从文件夹名里的 "26集" / "更新至26集" 读取集数提示。"""
    text = str(folder_name or "").strip()
    if not text:
        return None
    for match in _RE_FOLDER_EPISODE.finditer(text):
        try:
            number = int(match.group(1))
        except (TypeError, ValueError):
            continue
        if number <= 0 or number in _NON_EPISODE_NUMBERS or _is_year_like(number):
            continue
        return number
    return None


def parse_size(size_str: str) -> int:
    """Parse a human readable size such as ``100MB`` into bytes (0 when unknown)."""
    text = str(size_str or "").strip().upper()
    if not text:
        return 0
    for suffix, multiplier in sorted(_SIZE_UNITS.items(), key=lambda item: -len(item[0])):
        if text.endswith(suffix):
            try:
                return int(float(text[: -len(suffix)]) * multiplier)
            except (TypeError, ValueError):
                return 0
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return 0
