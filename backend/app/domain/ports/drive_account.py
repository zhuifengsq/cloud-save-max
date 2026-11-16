"""Structural account entity contract, independent of the ORM implementation."""
from datetime import datetime
from typing import Protocol


class DriveAccountRecord(Protocol):
    id: int
    name: str
    drive_type: str
    profile_json: str | None
    enabled: bool
    is_default: bool
    capacity_warning_threshold: int
    runtime_status: str | None
    probe_fail_count: int
    last_checked_at: datetime | None
    profile_updated_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime
    row_version: int

    @property
    def cookie(self) -> str: ...

    @property
    def config_json(self) -> str | None: ...

    def set_credentials(self, *, cookie: str, config_json: str | None) -> None: ...
