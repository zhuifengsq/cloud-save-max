from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SearchBatch:
    items: list[dict[str, Any]] = field(default_factory=list)
    sources: int = 0
    succeeded: int = 0
    timed_out: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class SuggestionResult:
    items: list[dict[str, Any]] = field(default_factory=list)
    changed: bool = False
    message: str | None = None
    partial: bool = False
    warnings: list[str] = field(default_factory=list)
