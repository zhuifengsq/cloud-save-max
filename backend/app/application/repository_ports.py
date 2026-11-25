"""Explicit repository APIs consumed by application services.

Provider JSON and legacy SQL projections remain Any at the data boundary; method
names, input contracts and scalar records are explicit, never ModuleType.
"""

from __future__ import annotations
from datetime import datetime
from typing import Any, Protocol
from collections.abc import Iterable, Sequence, Mapping
from sqlalchemy.orm import Session
from app.application import repository_records as records


class AuditRepository(Protocol):
    def add_audit_log(
        self,
        db: Session,
        *,
        actor_user_id: int | None,
        action: str,
        target_type: str | None = None,
        target_id: str | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
        success: bool = True,
        detail: str | None = None,
    ) -> None: ...
    def list_audit_logs(
        self,
        db: Session,
        *,
        page: int = 1,
        page_size: int = 20,
        q: str | None = None,
        action: str | None = None,
        success: bool | None = None,
    ) -> tuple[Sequence[Any], int]: ...
    def get_or_create_scheduler_setting(
        self, db: Session
    ) -> records.AuditLogSchedulerSettingRecord: ...
    def purge_old_audit_logs(self, db: Session, *, cutoff: datetime) -> int: ...
    def delete_all(self, db: Session) -> int: ...


class DriveAccountProbeSchedulerSettingRepository(Protocol):
    def get_setting(
        self, db: Session
    ) -> records.DriveAccountProbeSchedulerSettingRecord | None: ...
    def get_or_create_setting(
        self, db: Session
    ) -> records.DriveAccountProbeSchedulerSettingRecord: ...


class InvalidShareLinksRepository(Protocol):
    def get_invalid_share_link(
        self, db: Session, shareurl: str
    ) -> records.InvalidShareLinkRecord | None: ...
    def add_invalid_share_link(
        self, db: Session, *, shareurl: str
    ) -> records.InvalidShareLinkRecord: ...
    def list_invalid_shareurls(
        self, db: Session, *, shareurls: list[str]
    ) -> set[str]: ...
    def list_page(
        self,
        db: Session,
        *,
        page: int,
        page_size: int,
        q: str | None,
        drive_type: str | None,
    ) -> tuple[Sequence[records.InvalidShareLinkRecord], int]: ...
    def delete_by_url(self, db: Session, *, shareurl: str) -> int: ...
    def clear(self, db: Session, *, drive_type: str | None = None) -> int: ...


class MagicRegexRepository(Protocol):
    def get_rule(
        self, db: Session, key: str
    ) -> records.MagicRegexRuleRecord | None: ...
    def list_rules(self, db: Session) -> Sequence[records.MagicRegexRuleRecord]: ...
    def create_rule(
        self,
        db: Session,
        *,
        key: str,
        label: str | None,
        pattern: str,
        replace: str,
        enabled: bool,
    ) -> records.MagicRegexRuleRecord: ...
    def delete_rule(self, db: Session, row: Any) -> None: ...


class NotificationSettingsRepository(Protocol):
    def get_or_create_notification_setting(
        self, db: Session
    ) -> records.NotificationSettingRecord: ...


class PermissionsRepository(Protocol):
    def list_permissions(self, db: Session) -> Sequence[records.PermissionRecord]: ...
    def get_permission_by_code(
        self, db: Session, code: str
    ) -> records.PermissionRecord | None: ...
    def add_permission(
        self, db: Session, *, code: str, name: str
    ) -> records.PermissionRecord: ...
    def list_permissions_by_ids(
        self, db: Session, permission_ids: list[int]
    ) -> Sequence[records.PermissionRecord]: ...
    def list_permissions_by_codes(
        self, db: Session, codes: tuple[str, ...]
    ) -> Sequence[records.PermissionRecord]: ...
    def delete_permission(self, db: Session, permission: Any) -> None: ...


class RefreshTokensRepository(Protocol):
    def add_refresh_token(
        self,
        db: Session,
        *,
        user_id: int,
        token_hash: str,
        expires_at: datetime,
        ip: str | None,
        user_agent: str | None,
        last_used_at: datetime,
    ) -> records.RefreshTokenRecord: ...
    def get_by_token_hash(
        self, db: Session, token_hash: str
    ) -> records.RefreshTokenRecord | None: ...


class ResourceSearchSourcesRepository(Protocol):
    def list_api_sources(
        self, db: Session, *, key_prefix: str
    ) -> Sequence[records.ResourceSearchSourceRecord]: ...
    def list_enabled_api_sources(
        self, db: Session, *, key_prefix: str
    ) -> Sequence[records.ResourceSearchSourceRecord]: ...
    def get_resource_search_source(
        self, db: Session, source_id: int
    ) -> records.ResourceSearchSourceRecord | None: ...
    def create_resource_search_source(
        self, db: Session, *, key: str, enabled: bool, config_json: str
    ) -> records.ResourceSearchSourceRecord: ...
    def delete_resource_search_source(self, db: Session, row: Any) -> None: ...
    def ensure_plugin_rows(
        self, db: Session, keys: list[str]
    ) -> Mapping[str, records.ResourceSearchSourceRecord]: ...


class RolesRepository(Protocol):
    def list_roles(self, db: Session) -> Sequence[records.RoleRecord]: ...
    def get_role(self, db: Session, role_id: int) -> records.RoleRecord | None: ...
    def find_role_by_name(
        self, db: Session, name: str
    ) -> records.RoleRecord | None: ...
    def role_name_exists(self, db: Session, name: str) -> bool: ...
    def create_role(
        self, db: Session, *, name: str, description: str | None
    ) -> records.RoleRecord: ...
    def delete_role(self, db: Session, role: Any) -> None: ...
    def list_roles_by_ids(
        self, db: Session, role_ids: list[int]
    ) -> Sequence[records.RoleRecord]: ...


class SharePreviewBatchCacheRepository(Protocol):
    def get_cached_item(
        self, db: Session, *, shareurl: str
    ) -> records.SharePreviewBatchCacheRecord | None: ...
    def new_item(
        self, db: Session, *, shareurl: str, expires_at: datetime
    ) -> records.SharePreviewBatchCacheRecord: ...
    def delete_expired_before(self, db: Session, *, threshold: datetime) -> int: ...
    def list_items(
        self,
        db: Session,
        *,
        page: int,
        page_size: int,
        q: str | None,
        drive_type: str | None,
        ok: bool | None,
        expired_only: bool,
    ) -> tuple[Sequence[records.SharePreviewBatchCacheRecord], int]: ...
    def delete_by_shareurl(self, db: Session, *, shareurl: str) -> int: ...
    def delete_expired(self, db: Session, *, threshold: datetime) -> int: ...


class SystemSettingsRepository(Protocol):
    def claim_initial_admin(self, db: Session) -> bool: ...
    def get_row(self, db: Session, key: str) -> records.SystemSettingRecord | None: ...
    def get_value(self, db: Session, key: str, default: str = "") -> str: ...
    def list_values(self, db: Session, keys: list[str]) -> dict[str, str]: ...
    def list_drama_pins(self, db: Session) -> list[str]: ...
    def set_drama_pin(self, db: Session, group: str, pinned: bool) -> None: ...
    def set_value(
        self, db: Session, key: str, value: str, description: str = ""
    ) -> None: ...


class TaskExecutionsRepository(Protocol):
    def list_stale_execution_ids(
        self, db: Session, *, task_id: int, limit: int
    ) -> Sequence[int]: ...
    def delete_executions(self, db: Session, *, ids: list[int]) -> int: ...
    def list_distinct_task_ids(self, db: Session) -> Sequence[int]: ...
    def list_recent_adapter_snapshots(
        self, db: Session, *, task_id: int, limit: int
    ) -> Sequence[str | None]: ...
    def get_latest_for_task(
        self, db: Session, task_id: int
    ) -> records.TaskExecutionRecord | None: ...
    def list_desc(
        self, db: Session, task_id: int, *, limit: int
    ) -> Sequence[records.TaskExecutionRecord]: ...
    def delete_failed_drama_executions(
        self, db: Session, *, days: int = 0, before: datetime | None = None
    ) -> int: ...
    def read_page(self, db: Any, task_id: Any, *, page: Any, page_size: Any) -> Any: ...
    def read_recent_summaries(
        self, db: Any, task_ids: Any, limit: Any = 3, *, status: Any = None
    ) -> Any: ...
    def read_detail(
        self,
        db: Any,
        task_id: Any,
        execution_id: Any,
        *,
        log_offset: Any,
        log_limit: Any,
    ) -> Any: ...


class TaskLeadInRepository(Protocol):
    def get_record(
        self, db: Session, task_id: int, drive_type: str | None = None
    ) -> records.TaskLeadInRecord | None: ...
    def upsert_record(self, db: Session, **fields: Any) -> records.TaskLeadInRecord: ...
    def list_by_task_ids(
        self, db: Session, task_ids: list[int]
    ) -> Sequence[records.TaskLeadInRecord]: ...


class TaskReadsRepository(Protocol):
    def candidates(
        self,
        db: Any,
        *,
        q: Any = "",
        task_type: Any = None,
        enabled: Any = None,
        drive_type: Any = None,
        language: Any = "zh-CN",
        poster_language: Any = "zh-CN",
    ) -> Any: ...
    def read_tasks(self, db: Any, ids: Any, *, full: Any = False) -> Any: ...
    def task_exists(self, db: Any, task_id: Any) -> Any: ...


class TaskSchedulerSettingRepository(Protocol):
    def get_setting(self, db: Session) -> records.TaskSchedulerSettingRecord | None: ...
    def get_or_create_setting(
        self, db: Session
    ) -> records.TaskSchedulerSettingRecord: ...


class TaskTemplatesRepository(Protocol):
    def list_rows(self, db: Session) -> Sequence[records.TaskTemplateRecord]: ...
    def get_row(
        self, db: Session, template_id: int
    ) -> records.TaskTemplateRecord | None: ...
    def create_row(
        self, db: Session, *, name: str, config_json: str
    ) -> records.TaskTemplateRecord: ...
    def delete_row(self, db: Session, row: Any) -> None: ...


class TasksRepository(Protocol):
    def iter_drama_tasks_without_executions(
        self, db: Session
    ) -> Iterable[records.TaskRecord]: ...
    def list_drama_tasks(self, db: Session) -> Sequence[records.TaskRecord]: ...
    def list_drama_execution_rows(self, db: Session, *, since: Any) -> Any: ...
    def list_tasks(self, db: Session) -> Sequence[records.TaskRecord]: ...
    def list_tasks_recent_executions(
        self, db: Session, *, limit: int = 3
    ) -> Sequence[records.TaskRecord]: ...
    def get_task_row(self, db: Session, task_id: int) -> records.TaskRecord | None: ...
    def guard_task_edit(self, db: Session, task: records.TaskRecord) -> bool: ...
    def list_recent_executions(
        self, db: Session, task_id: int, *, limit: int = 3
    ) -> Sequence[records.TaskExecutionRecord]: ...
    def create_task_row(
        self,
        db: Session,
        *,
        task_uid: str,
        task_type: str,
        taskname: str,
        shareurl: str,
        savepath: str,
        pattern: str | None,
        replace: str | None,
        ignore_extension: bool,
        account_name: str | None,
        tmdb_id: int | None,
        tmdb_media_type: str | None,
        enabled: bool,
        addition_json: str,
        extra_json: str,
    ) -> records.TaskRecord: ...
    def delete_task_row(self, db: Session, task: Any) -> None: ...
    def count_tasks(self, db: Session) -> int: ...
    def count_enabled_tasks(self, db: Session) -> int: ...
    def get_first_by_shareurls(
        self, db: Session, urls: list[str]
    ) -> records.TaskRecord | None: ...
    def build_task_row(self, **kwargs: Any) -> records.TaskRecord: ...
    def stop_completed_drama_tasks(
        self, db: Session, *, language: str, poster_language: str
    ) -> dict[str, Any]: ...


class TelegramBotSessionRepository(Protocol):
    def get_or_create_bot_state(
        self, db: Session
    ) -> records.TelegramBotStateRecord: ...
    def get_or_create_session(
        self, db: Session, *, chat_id: int, user_id: int
    ) -> records.TelegramBotSessionRecord: ...


class TmdbCacheSchedulerSettingRepository(Protocol):
    def get_setting(
        self, db: Session
    ) -> records.TMDBCacheSchedulerSettingRecord | None: ...
    def get_or_create_setting(
        self, db: Session
    ) -> records.TMDBCacheSchedulerSettingRecord: ...


class TmdbMediaCacheRepository(Protocol):
    def load_status_rows(
        self,
        db: Session,
        *,
        language: str,
        poster_language: str,
        tv_ids: list[int],
        movie_ids: list[int],
    ) -> Any: ...
    def load_payload_rows(
        self,
        db: Session,
        *,
        language: str,
        poster_language: str,
        tv_ids: list[int],
        movie_ids: list[int] | None = None,
    ) -> Any: ...
    def load_update_weekday_rows(
        self, db: Session, *, language: str, poster_language: str, tv_ids: list[int]
    ) -> Any: ...
    def get_latest_schedule_row(
        self,
        db: Session,
        *,
        media_type: str,
        tmdb_id: int,
        language: str,
        poster_language: str,
    ) -> Any: ...
    def get_latest_status(
        self,
        db: Session,
        *,
        media_type: str,
        tmdb_id: int,
        language: str,
        poster_language: str,
    ) -> Any: ...
    def get_row(
        self,
        db: Session,
        *,
        media_type: str,
        tmdb_id: int,
        language: str,
        poster_language: str,
    ) -> records.TMDBMediaCacheRecord | None: ...


class TmdbSettingsRepository(Protocol):
    def get_or_create_tmdb_setting(self, db: Session) -> records.TMDBSettingRecord: ...


class UsersRepository(Protocol):
    def list_users(
        self, db: Session, *, page: int, page_size: int, q: str | None
    ) -> tuple[Sequence[records.UserRecord], int]: ...
    def has_any_user(self, db: Session) -> bool: ...
    def get_user(self, db: Session, user_id: int) -> records.UserRecord | None: ...
    def get_user_by_username(
        self, db: Session, username: str
    ) -> records.UserRecord | None: ...
    def username_or_email_exists(
        self, db: Session, *, username: str, email: str
    ) -> bool: ...
    def create_user(
        self, db: Session, *, username: str, email: str, hashed_password: str
    ) -> records.UserRecord: ...
    def list_users_by_ids(
        self, db: Session, *, user_ids: list[int], with_roles: bool = False
    ) -> Sequence[records.UserRecord]: ...
    def get_active_user_basic(
        self, db: Session, user_id: int
    ) -> records.UserRecord | None: ...
    def load_roles_permissions(
        self, db: Session, user_id: int
    ) -> tuple[Sequence[str], set[str]]: ...
