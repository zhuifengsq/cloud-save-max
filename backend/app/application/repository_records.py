"""Application-facing scalar record shapes; ORM implementations stay in persistence."""

from __future__ import annotations
from datetime import datetime
from typing import Any, Protocol
from collections.abc import Sequence


class AuditLogSchedulerSettingRecord(Protocol):
    id: int
    enabled: bool
    crontab: str
    timezone: str
    retention_days: int
    created_at: datetime
    updated_at: datetime


class DriveAccountProbeSchedulerSettingRecord(Protocol):
    id: int
    enabled: bool
    crontab: str
    timezone: str
    enabled_only: bool
    created_at: datetime
    updated_at: datetime


class InvalidShareLinkRecord(Protocol):
    shareurl: str
    drive_type: str | None
    message: str | None
    hit_count: int
    created_at: datetime
    updated_at: datetime


class MagicRegexRuleRecord(Protocol):
    id: int
    key: str
    label: str | None
    pattern: str
    replace: str
    enabled: bool
    created_at: datetime
    updated_at: datetime


class NotificationSettingRecord(Protocol):
    id: int
    config_json: str | None
    created_at: datetime
    updated_at: datetime


class PermissionRecord(Protocol):
    id: int
    code: str
    name: str
    description: str | None


class RefreshTokenRecord(Protocol):
    id: int
    user_id: int
    token_hash: str
    expires_at: datetime
    revoked_at: datetime | None
    last_used_at: datetime | None
    ip: str | None
    user_agent: str | None
    created_at: datetime


class ResourceSearchSourceRecord(Protocol):
    id: int
    key: str
    enabled: bool
    config_json: str
    created_at: datetime
    updated_at: datetime


class RoleRecord(Protocol):
    @property
    def permissions(self) -> Sequence[Any]: ...

    id: int
    name: str
    description: str | None


class SharePreviewBatchCacheRecord(Protocol):
    shareurl: str
    drive_type: str | None
    ok: bool
    message: str | None
    checked_at: datetime
    expires_at: datetime
    hit_count: int
    created_at: datetime
    updated_at: datetime


class SystemSettingRecord(Protocol):
    id: int
    key: str
    value: str
    description: str | None
    created_at: datetime
    updated_at: datetime


class TaskRecord(Protocol):
    @property
    def executions(self) -> Sequence[Any]: ...

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
    created_at: datetime
    updated_at: datetime


class TaskExecutionRecord(Protocol):
    id: int
    task_id: int
    status: str
    started_at: datetime
    finished_at: datetime | None
    tree_summary: str | None
    message: str | None
    stage: str | None
    run_log: str | None
    adapter_snapshot: str | None
    created_at: datetime


class TaskSchedulerSettingRecord(Protocol):
    id: int
    enabled: bool
    crontab: str
    timezone: str
    created_at: datetime
    updated_at: datetime


class TaskTemplateRecord(Protocol):
    id: int
    name: str
    config_json: str
    created_at: datetime
    updated_at: datetime


class TelegramBotSessionRecord(Protocol):
    id: int
    chat_id: int
    user_id: int
    scene: str
    step: str
    context_json: str
    last_message_id: int | None
    created_at: datetime
    updated_at: datetime


class TelegramBotStateRecord(Protocol):
    id: int
    last_update_id: int
    last_error: str | None
    last_polled_at: datetime | None
    last_processed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TMDBCacheSchedulerSettingRecord(Protocol):
    id: int
    enabled: bool
    crontab: str
    timezone: str
    max_items_per_run: int
    only_refresh_linked_tasks: bool
    retention_days: int
    created_at: datetime
    updated_at: datetime


class TMDBMediaCacheRecord(Protocol):
    id: int
    media_type: str
    tmdb_id: int
    language: str
    poster_language: str
    payload_json: str | None
    update_weekdays_json: str | None
    display_title: str | None
    original_title: str | None
    year: str | None
    status: str | None
    first_air_date: str | None
    last_air_date: str | None
    release_date: str | None
    next_episode_air_date: str | None
    poster_path: str | None
    vote_average: float | None
    vote_count: int | None
    fetched_at: datetime | None
    expires_at: datetime | None
    last_accessed_at: datetime | None
    refresh_in_progress: bool
    refresh_started_at: datetime | None
    fail_count: int
    last_error: str | None
    credits_json: str | None
    credits_fetched_at: datetime | None
    credits_expires_at: datetime | None
    credits_fail_count: int
    credits_last_error: str | None
    credits_refresh_started_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TMDBSettingRecord(Protocol):
    id: int
    config_json: str | None
    created_at: datetime
    updated_at: datetime


class UserRecord(Protocol):
    @property
    def roles(self) -> Sequence[Any]: ...

    id: int
    username: str
    email: str
    hashed_password: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class TaskLeadInRecord(Protocol):
    id: int
    task_id: int
    drive_type: str
    account_name: str
    dir_path: str
    material_name: str
    material_fid: str
    status: str
    message: str
    config_version: int
    created_at: datetime
    updated_at: datetime
