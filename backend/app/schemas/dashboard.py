from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.task_scheduler import TaskSchedulerSettingOut


class CapacitySummaryOut(BaseModel):
    account_count: int
    enabled_account_count: int
    capacity_account_count: int
    warning_account_count: int
    total_used_space: int | None = None
    total_space: int | None = None
    usage_ratio: float | None = None


class CapacityAccountOut(BaseModel):
    id: int
    name: str
    drive_type: str
    config: dict[str, Any] = {}
    profile: dict[str, Any] = {}
    enabled: bool
    is_default: bool
    capacity_warning_threshold: int
    used_space: int | None = None
    total_space: int | None = None
    usage_ratio: float | None = None
    runtime_status: str | None = None
    last_checked_at: datetime | None = None
    profile_updated_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class CapacityOverviewOut(BaseModel):
    summary: CapacitySummaryOut
    accounts: list[CapacityAccountOut]
    warning_accounts: list[CapacityAccountOut]
    unsupported_accounts: list[CapacityAccountOut]
    updated_at: datetime | None = None


class DramaSummaryOut(BaseModel):
    task_count: int
    enabled_task_count: int
    tmdb_bound_count: int
    unknown_schedule_count: int
    monthly_success_count: int
    window_days: int
    execution_total: int
    execution_success: int
    execution_failed: int
    execution_skipped: int
    success_rate: float | None = None
    avg_duration_s: float | None = None


class DramaTrendPointOut(BaseModel):
    date: str
    total: int
    success: int
    failed: int
    skipped: int
    avg_duration_s: float | None = None


class DramaFailureItemOut(BaseModel):
    task_id: int
    taskname: str
    status: str
    started_at: datetime
    stage: str | None = None
    message: str | None = None


class DramaUpdateItemOut(BaseModel):
    task_id: int
    started_at: datetime
    drive_type: str | None = None
    transfer_count: int = 0
    new_files: list[str] = Field(default_factory=list)


class DramaUpdateDriveItemOut(BaseModel):
    task_id: int
    drive_type: str | None = None
    account_name: str | None = None
    started_at: datetime
    transfer_count: int = 0
    new_files: list[str] = Field(default_factory=list)
    execution_count: int = 1


class DramaUpdateGroupOut(BaseModel):
    drama_key: str
    tmdb_id: int | None = None
    title: str
    latest_at: datetime
    total_transfer_count: int = 0
    drives: list[DramaUpdateDriveItemOut] = Field(default_factory=list)


class DramaUpdatesResponseOut(BaseModel):
    range: Literal["today", "24h", "7d", "30d"]
    since: datetime
    drama_count: int = 0
    drive_update_count: int = 0
    total_transfer_count: int = 0
    groups: list[DramaUpdateGroupOut] = Field(default_factory=list)


class DramaShareurlUpdateLogOut(BaseModel):
    task_id: int
    taskname: str
    id: str
    at: str
    source: Literal["auto", "manual"]
    status: Literal["success", "failed"]
    message: str
    old_shareurl: str
    new_shareurl: str
    round: int | None = None


class DramaOverviewOut(BaseModel):
    scheduler: TaskSchedulerSettingOut
    summary: DramaSummaryOut
    trend: list[DramaTrendPointOut]
    recent_updates: list[DramaUpdateItemOut] = Field(default_factory=list)
    recent_failures: list[DramaFailureItemOut]
    shareurl_update_logs: list[DramaShareurlUpdateLogOut] = Field(default_factory=list)
    updated_at: datetime | None = None
