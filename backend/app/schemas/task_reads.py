"""B2 explicit summary and execution detail contracts."""

from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field
from app.schemas.task import TaskOut


class TaskExecutionSummaryOut(BaseModel):
    id: int
    task_id: int
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    message: str | None = None
    stage: str | None = None
    adapter_snapshot: dict[str, Any] = Field(default_factory=dict)


class TaskSummaryOut(TaskOut):
    executions: list[TaskExecutionSummaryOut] = Field(default_factory=list)
    drive_type: str = ""


class TaskOverviewOut(TaskSummaryOut):
    pass


class TaskFacetsOut(BaseModel):
    categories: list[str]
    drives: list[str]
    failed_count: int


class TaskPageOut(BaseModel):
    items: list[TaskSummaryOut]
    total: int
    page: int
    page_size: int
    facets: TaskFacetsOut


class TaskOrderIndexItemOut(BaseModel):
    id: int
    taskname: str
    drive_type: str


class TaskOrderIndexOut(BaseModel):
    items: list[TaskOrderIndexItemOut]


class TaskExecutionPageOut(BaseModel):
    items: list[TaskExecutionSummaryOut]
    total: int
    page: int
    page_size: int


class TaskExecutionDetailOut(TaskExecutionSummaryOut):
    tree_summary: str
    run_log: str
    log_next_offset: int
    log_complete: bool
    log_truncated: bool
    log_dropped_bytes: int
    tree_truncated: bool
