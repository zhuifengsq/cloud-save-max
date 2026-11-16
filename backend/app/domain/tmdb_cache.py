"""Framework-independent cache row and writer policy contracts."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class WriterOptions:
    connect_timeout: float = 2.0
    slots_timeout: float = 3.0
    retry_delay: float = 0.2


class CacheSetting(Protocol):
    config_json: str | None


class CacheRow(Protocol):
    id: int
    payload_json: str | None
    update_weekdays_json: str | None
    expires_at: datetime | None
    last_accessed_at: datetime | None
    refresh_in_progress: bool
    refresh_started_at: datetime | None
    fail_count: int
    credits_json: str | None
    credits_expires_at: datetime | None
    credits_fail_count: int
