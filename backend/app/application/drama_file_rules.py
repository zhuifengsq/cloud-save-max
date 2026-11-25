"""File-level rules for ordinary drama preview and transfer planning."""
from __future__ import annotations

import os
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import SupportsFloat, SupportsIndex

from app.application.episode_number import extract_episode
from app.domain.ports.share_preview import PreviewRenamerPort
from app.domain.share_entries import is_episode_file_name


def split_words(value: object) -> tuple[str, ...]:
    return tuple(word for part in str(value or "").lower().split("|") if (word := part.strip()))


def file_before_date(value: object, minimum: str) -> bool:
    if value is None:
        return False
    try:
        if isinstance(value, (int, float)) and value > 0:
            date = datetime.fromtimestamp(value / 1000 if value > 1e12 else value).date().isoformat()
        else:
            date = str(value)[:10]
        return date < minimum
    except (TypeError, ValueError, OverflowError, OSError):
        return False


@dataclass(frozen=True)
class FileFilters:
    min_bytes: int = 0
    blocked: tuple[str, ...] = ()
    required: tuple[str, ...] = ()
    match_any: bool = False
    min_date: str = ""

    def rejection(self, name: str, size: int | None, updated_at: object) -> str | None:
        if self.min_bytes > 0 and (size or 0) < self.min_bytes:
            return "size"
        lower_name = name.lower()
        if self.blocked and any(word in lower_name for word in self.blocked):
            return "keyword"
        matches = any if self.match_any else all
        if self.required and not matches(word in lower_name for word in self.required):
            return "file_filter"
        if self.min_date and file_before_date(updated_at, self.min_date):
            return "file_date"
        return None


def rename_candidate(name: str, *, match: re.Pattern[str] | None, replace: str, renamer: PreviewRenamerPort) -> str | None:
    if match is not None and match.search(name) is None:
        return None
    return renamer.sub(match.pattern if match is not None else "", replace, name)


def transfer_target_key(name: str, ignore_extension: bool) -> tuple[bool, str]:
    episode_file = is_episode_file_name(name)
    return episode_file, os.path.splitext(name)[0] if ignore_extension and episode_file else name


class ExistingDramaFiles:
    def __init__(
        self, names: Sequence[str], *, ignore_extension: bool,
        search: re.Pattern[str] | None, replace: str, renamer: PreviewRenamerPort,
    ):
        self.names = list(names)
        self.exact = set(names)
        self.ignore_extension = ignore_extension
        self.renamer = renamer
        self.by_target = {transfer_target_key(name, ignore_extension): name for name in names}
        self.episodes: dict[tuple[int | None, int | None], str] = {}
        for name in names:
            if is_episode_file_name(name):
                season, episode = extract_episode(name)
                if season is None and (search is not None or replace):
                    target = rename_candidate(name, match=search, replace=replace, renamer=renamer)
                    if target:
                        season, episode = extract_episode(target)
                if season is not None and episode is not None:
                    self.episodes[(season, episode)] = name

    def match(self, origin: str, target: str) -> str | None:
        if origin in self.exact:
            return origin
        episode_file = is_episode_file_name(target)
        if existing := self.by_target.get(transfer_target_key(target, self.ignore_extension)):
            return existing
        if is_episode_file_name(origin) and episode_file:
            episode = extract_episode(target)
            if episode[0] is None:
                episode = extract_episode(origin)
            if existing := self.episodes.get(episode):
                return existing
        if re.search(r"\{I+\}", target):
            same_type = [name for name in self.names if is_episode_file_name(name) == episode_file]
            return self.renamer.is_exists(target, same_type, self.ignore_extension and episode_file)
        return None


def conflict_winners(rows: Iterable[tuple[str | None, int | None, object]], ignore_extension: bool) -> set[int]:
    best: dict[tuple[bool, str], tuple[float, float, int]] = {}
    for idx, (target, size, updated_at) in enumerate(rows):
        if not target:
            continue
        key = transfer_target_key(target, ignore_extension)
        try:
            numeric = isinstance(updated_at, (SupportsFloat, SupportsIndex, str, bytes, bytearray))
            timestamp = float(updated_at) if numeric else float("-inf")
        except (TypeError, ValueError, OverflowError):
            timestamp = float("-inf")
        score = (float(size) if size is not None else float("-inf"), timestamp, idx)
        if key not in best or score > best[key]:
            best[key] = score
    return {score[2] for score in best.values()}
