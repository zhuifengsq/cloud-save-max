"""Detached task execution values and structural contracts, independent of application/ORM."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Protocol

@dataclass(frozen=True)
class RunTaskCommand:
    task_id: int
    actor_user_id: int | None = None
    ip: str | None = None
    user_agent: str | None = None
    preview: dict[str, Any] | None = None

@dataclass
class TaskRunResult:
    id: int = 0
    task_id: int = 0
    status: str = ''
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: datetime | None = None
    tree_summary: str | None = None
    message: str | None = None
    stage: str | None = None
    run_log: str | None = None
    adapter_snapshot: dict[str, Any] = field(default_factory=dict)

@dataclass
class TaskRunState:
    id: int = 0
    task_uid: str = ''
    task_type: str = 'generic'
    taskname: str = ''
    shareurl: str = ''
    savepath: str = ''
    pattern: str | None = None
    replace: str | None = None
    ignore_extension: bool = False
    account_name: str | None = None
    tmdb_id: int | None = None
    tmdb_media_type: str | None = None
    enabled: bool = True
    addition_json: str | None = None
    extra_json: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

@dataclass
class TaskRunPlan:
    task: TaskRunState
    prepared: dict[str, Any]


class TaskExecutionState(Protocol):
    """State shared by persisted tasks and detached execution plans."""
    id: int
    task_uid: str
    task_type: str
    taskname: str
    shareurl: str
    savepath: str
    pattern: str | None
    replace: str | None
    ignore_extension: bool
    account_name: str | None
    tmdb_id: int | None
    tmdb_media_type: str | None
    enabled: bool
    addition_json: str | None
    extra_json: str | None


class RunPolicyPort(Protocol):
    @property
    def trigger(self) -> str: ...
    @property
    def share_check_always_notify(self) -> bool: ...
    @property
    def persist_execution(self) -> bool: ...
    @property
    def manual_run(self) -> bool: ...


class ExecutionLogPort(Protocol):
    started_at: datetime
    stage: str | None
    def set_stage(self, stage: str | None) -> None: ...
    def section(self, title: str) -> None: ...
    def line(self, text: str = "") -> None: ...
    def progress(self, payload: dict[str, Any]) -> None: ...
    def render(self) -> str: ...
    def metadata(self) -> dict[str, Any]: ...


RecoveryCallback = Callable[[TaskExecutionState, ExecutionLogPort, set[str] | None], bool]
