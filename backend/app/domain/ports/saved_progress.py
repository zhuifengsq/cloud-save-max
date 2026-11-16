"""Detached values and ports for correcting one task's saved episode watermark."""
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.domain.ports.task_share import TaskSharePlan


@dataclass(frozen=True)
class SavedLatestProgress:
    episode: int
    season: int | None
    name: str
    at: str


@dataclass(frozen=True)
class SavedProgressPlan:
    task_id: int
    drive: TaskSharePlan = field(repr=False)
    # Detached provider/task JSON snapshots; schemas vary across providers.
    task_snapshot: dict[str, Any] = field(repr=False)
    task_data: dict[str, Any] = field(repr=False)
    account_snapshot: dict[str, Any] = field(repr=False)
    magic_snapshot: tuple[dict[str, Any], ...] = field(repr=False)


@dataclass(frozen=True)
class SavedProgressScan:
    saved_latest: SavedLatestProgress
    scanned_files: int
    scanned_directories: int


@dataclass(frozen=True)
class SavedProgressCorrection:
    task_id: int
    previous_episode: int | None
    saved_latest: SavedLatestProgress
    scanned_files: int
    scanned_directories: int


class SavedProgressStorePort(Protocol):
    def prepare(self, task_id: int) -> SavedProgressPlan: ...
    def save(self, plan: SavedProgressPlan, scan: SavedProgressScan) -> None: ...


class SavedProgressScannerPort(Protocol):
    def scan(self, plan: SavedProgressPlan) -> SavedProgressScan: ...
