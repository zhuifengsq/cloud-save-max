"""Detached account operation state; secrets are omitted from representations."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class AccountOperation:
    id: int
    row_version: int
    name: str
    drive_type: str
    config: dict[str, Any] = field(repr=False)
    cookie: str = field(repr=False)


@dataclass
class ProbeOutcome:
    status: str
    checked_at: datetime
    error: str | None = None
    config: dict[str, Any] | None = field(default=None, repr=False)
    profile: dict[str, Any] | None = None
    profile_updated_at: datetime | None = None
