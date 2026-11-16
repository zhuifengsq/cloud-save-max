"""Static composition checks against real implementations, without casts.

This file is checked by mypy and is never imported by the running application.
"""

from app.application.drive_account_types import (
    AccountAdapterFactory,
    AccountRegistry,
    AccountRepository,
)
from app.domain.ports.drive_account import DriveAccountRecord
from app.domain.ports.task import TaskExecutionEnginePort, TaskRunStorePort
from app.domain.task_run import TaskExecutionState
from app.infrastructure.drive.adapter_registry import AdapterRegistry
from app.infrastructure.drive.factory import AdapterFactory
from app.infrastructure.execution.task_run_stages import (
    PreparedTaskEngine,
    SqlTaskRunStore,
)
from app.infrastructure.persistence.models.drive_account import DriveAccount
from app.infrastructure.persistence.models.task import Task
from app.infrastructure.persistence.repositories import drive_accounts
from sqlalchemy.orm import Session


def check_bindings(db: Session, account: DriveAccount, task: Task) -> None:
    repository: AccountRepository = drive_accounts
    factory: AccountAdapterFactory = AdapterFactory
    registry: AccountRegistry = AdapterRegistry
    record: DriveAccountRecord = account
    execution_state: TaskExecutionState = task
    store: TaskRunStorePort = SqlTaskRunStore(db)
    engine: TaskExecutionEnginePort = PreparedTaskEngine()
    # Keep names live for Ruff too; none of these checks execute at runtime.
    assert (
        repository
        and factory
        and registry
        and record
        and execution_state
        and store
        and engine
    )


from app.application import repository_ports
from app.infrastructure.persistence.repositories import audit
from app.infrastructure.persistence.repositories import (
    drive_account_probe_scheduler_setting,
)
from app.infrastructure.persistence.repositories import invalid_share_links
from app.infrastructure.persistence.repositories import magic_regex
from app.infrastructure.persistence.repositories import notification_settings
from app.infrastructure.persistence.repositories import permissions
from app.infrastructure.persistence.repositories import refresh_tokens
from app.infrastructure.persistence.repositories import resource_search_sources
from app.infrastructure.persistence.repositories import roles
from app.infrastructure.persistence.repositories import share_preview_batch_cache
from app.infrastructure.persistence.repositories import system_settings
from app.infrastructure.persistence.repositories import task_executions
from app.infrastructure.persistence.repositories import task_lead_in
from app.infrastructure.persistence.repositories import task_reads
from app.infrastructure.persistence.repositories import task_scheduler_setting
from app.infrastructure.persistence.repositories import task_templates
from app.infrastructure.persistence.repositories import tasks
from app.infrastructure.persistence.repositories import telegram_bot_session
from app.infrastructure.persistence.repositories import tmdb_cache_scheduler_setting
from app.infrastructure.persistence.repositories import tmdb_media_cache
from app.infrastructure.persistence.repositories import tmdb_settings
from app.infrastructure.persistence.repositories import users


def check_repository_bindings() -> None:
    audit_port: repository_ports.AuditRepository = audit
    drive_account_probe_scheduler_setting_port: repository_ports.DriveAccountProbeSchedulerSettingRepository = drive_account_probe_scheduler_setting
    invalid_share_links_port: repository_ports.InvalidShareLinksRepository = (
        invalid_share_links
    )
    magic_regex_port: repository_ports.MagicRegexRepository = magic_regex
    notification_settings_port: repository_ports.NotificationSettingsRepository = (
        notification_settings
    )
    permissions_port: repository_ports.PermissionsRepository = permissions
    refresh_tokens_port: repository_ports.RefreshTokensRepository = refresh_tokens
    resource_search_sources_port: repository_ports.ResourceSearchSourcesRepository = (
        resource_search_sources
    )
    roles_port: repository_ports.RolesRepository = roles
    share_preview_batch_cache_port: repository_ports.SharePreviewBatchCacheRepository = share_preview_batch_cache
    system_settings_port: repository_ports.SystemSettingsRepository = system_settings
    task_executions_port: repository_ports.TaskExecutionsRepository = task_executions
    task_lead_in_port: repository_ports.TaskLeadInRepository = task_lead_in
    task_reads_port: repository_ports.TaskReadsRepository = task_reads
    task_scheduler_setting_port: repository_ports.TaskSchedulerSettingRepository = (
        task_scheduler_setting
    )
    task_templates_port: repository_ports.TaskTemplatesRepository = task_templates
    tasks_port: repository_ports.TasksRepository = tasks
    telegram_bot_session_port: repository_ports.TelegramBotSessionRepository = (
        telegram_bot_session
    )
    tmdb_cache_scheduler_setting_port: repository_ports.TmdbCacheSchedulerSettingRepository = tmdb_cache_scheduler_setting
    tmdb_media_cache_port: repository_ports.TmdbMediaCacheRepository = tmdb_media_cache
    tmdb_settings_port: repository_ports.TmdbSettingsRepository = tmdb_settings
    users_port: repository_ports.UsersRepository = users
    assert (
        audit_port
        and drive_account_probe_scheduler_setting_port
        and invalid_share_links_port
        and magic_regex_port
        and notification_settings_port
        and permissions_port
        and refresh_tokens_port
        and resource_search_sources_port
        and roles_port
        and share_preview_batch_cache_port
        and system_settings_port
        and task_executions_port
        and task_lead_in_port
        and task_reads_port
        and task_scheduler_setting_port
        and task_templates_port
        and tasks_port
        and telegram_bot_session_port
        and tmdb_cache_scheduler_setting_port
        and tmdb_media_cache_port
        and tmdb_settings_port
        and users_port
    )
