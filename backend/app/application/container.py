"""应用层装配入口（composition）——唯一允许接触 infrastructure 实现的位置。

约束（见 docs/architecture-evolution-plan.md §3.1）：
- 延迟 import 只发生在本文件，且都是函数内 import；分层门禁把这类边记为
  deferred_imports（可见、可预算），不会把业务模块标成 application→infrastructure 违规。
- 单进程假设：这里返回的是无状态模块/单例，不做跨进程共享。
"""
from __future__ import annotations

from contextlib import contextmanager

from types import ModuleType
from typing import Any


_SYSTEM_CLOCK: Any = None

# ---------------------------------------------------------------------------
# 请求作用域数据库依赖的装配出口（composition root re-export）
# presentation 层（app/api/**）只从这里取 get_db / transaction / commit / SessionLocal，
# 不再直接 import persistence 实现；装配点的边不计入分层预算（check_layering 跳过
# composition-root）。S5 之前这些符号由路由直接从 app.infrastructure.persistence.session 导入。
from app.infrastructure.persistence.session import (  # noqa: E402
    SessionLocal,
    commit,
    get_db,
    transaction,
)


def audit_repo() -> ModuleType:
    """审计日志仓储（audit_logs / audit_log_scheduler / write_audit_log）。"""
    from app.infrastructure.persistence.repositories import audit

    return audit


def permissions_repo() -> ModuleType:
    """权限字典仓储。"""
    from app.infrastructure.persistence.repositories import permissions

    return permissions


def magic_regex_repo() -> ModuleType:
    """魔术正则规则仓储。"""
    from app.infrastructure.persistence.repositories import magic_regex

    return magic_regex


def notification_settings_repo() -> ModuleType:
    """通知设置仓储。"""
    from app.infrastructure.persistence.repositories import notification_settings

    return notification_settings


def system_settings_repo() -> ModuleType:
    """系统设置键值仓储（system_settings 表）。"""
    from app.infrastructure.persistence.repositories import system_settings

    return system_settings


def task_templates_repo() -> ModuleType:
    """任务模板仓储（task_templates 表）。"""
    from app.infrastructure.persistence.repositories import task_templates

    return task_templates


def task_lead_in_repo() -> ModuleType:
    """追剧引流记录仓储（task_lead_in 表）。"""
    from app.infrastructure.persistence.repositories import task_lead_in

    return task_lead_in


def task_executions_repo() -> ModuleType:
    """任务执行记录仓储。"""
    from app.infrastructure.persistence.repositories import task_executions

    return task_executions


def task_scheduler_setting_repo() -> ModuleType:
    """任务调度设置仓储。"""
    from app.infrastructure.persistence.repositories import task_scheduler_setting

    return task_scheduler_setting


def tmdb_cache_scheduler_setting_repo() -> ModuleType:
    """TMDB 缓存调度设置仓储。"""
    from app.infrastructure.persistence.repositories import tmdb_cache_scheduler_setting

    return tmdb_cache_scheduler_setting


def resource_search_sources_repo() -> ModuleType:
    """搜索源仓储（内置 API 源与搜索插件共用）。"""
    from app.infrastructure.persistence.repositories import resource_search_sources

    return resource_search_sources


def invalid_share_links_repo() -> ModuleType:
    """失效分享链接仓储。"""
    from app.infrastructure.persistence.repositories import invalid_share_links

    return invalid_share_links


def drive_account_probe_scheduler_setting_repo() -> ModuleType:
    """网盘账号探测调度设置仓储。"""
    from app.infrastructure.persistence.repositories import drive_account_probe_scheduler_setting

    return drive_account_probe_scheduler_setting


def tmdb_settings_repo() -> ModuleType:
    """TMDB 设置仓储。"""
    from app.infrastructure.persistence.repositories import tmdb_settings

    return tmdb_settings


def drive_accounts_repo() -> ModuleType:
    """网盘账号仓储。"""
    from app.infrastructure.persistence.repositories import drive_accounts

    return drive_accounts


def tasks_repo() -> ModuleType:
    """任务仓储。"""
    from app.infrastructure.persistence.repositories import tasks

    return tasks


def roles_repo() -> ModuleType:
    """角色仓储。"""
    from app.infrastructure.persistence.repositories import roles

    return roles


def users_repo() -> ModuleType:
    """用户仓储。"""
    from app.infrastructure.persistence.repositories import users

    return users


def refresh_tokens_repo() -> ModuleType:
    """刷新令牌仓储。"""
    from app.infrastructure.persistence.repositories import refresh_tokens

    return refresh_tokens


def telegram_bot_session_repo() -> ModuleType:
    """Telegram bot 状态与会话仓储。"""
    from app.infrastructure.persistence.repositories import telegram_bot_session

    return telegram_bot_session


def share_preview_batch_cache_repo() -> ModuleType:
    """分享预览批量缓存仓储。"""
    from app.infrastructure.persistence.repositories import share_preview_batch_cache

    return share_preview_batch_cache


def clock():
    """系统时钟（满足 app.domain.ports.clock.ClockPort）。

    platform 实现延迟 import：application 层只依赖端口形状，不依赖实现模块；
    单进程无状态，模块级缓存一个实例即可。测试可 monkeypatch 本函数注入假时钟。
    """
    global _SYSTEM_CLOCK
    from app.core.runtime.clock import SystemClock

    if _SYSTEM_CLOCK is None:
        _SYSTEM_CLOCK = SystemClock()
    return _SYSTEM_CLOCK


@contextmanager
def db_session():
    """显式开启一个会话（供确需自管会话的服务/扩展边界使用，单进程无 DI）。"""
    from app.infrastructure.persistence.session import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def tmdb_media_cache_repo() -> ModuleType:
    """TMDB 媒体缓存仓储。"""
    from app.infrastructure.persistence.repositories import tmdb_media_cache

    return tmdb_media_cache


def adapter_registry():
    """适配器注册表（网盘类型探测 + 适配器类注册），实现位于 infrastructure。

    业务层只经过本函数拿注册表，避免application顶层import infrastructure（§10.3 决策 4）。
    """
    from app.infrastructure.drive.adapter_registry import AdapterRegistry

    return AdapterRegistry

def drive_gateway(adapter: Any, *, drive_type: str | None = None):
    """把适配器包装成 `app.domain.ports.drive.DriveGatewayPort`（网盘网关端口）。

    application 层只认端口形状；实现放在 infrastructure，靠本函数的延迟 import
    保持 services 顶层依赖方向不变（§10.3 决策 4）。单进程无状态，调用方拿到的是
    与适配器一一对应的薄包装，用完调 close() 只关适配器连接，不影响实例缓存。
    """
    from app.infrastructure.drive.gateway import gateway_for_adapter

    return gateway_for_adapter(adapter, drive_type=drive_type)

# ---------------------------------------------------------------------------
# infrastructure 实现的装配出口（S5：presentation/application 不再顶层 import 实现）
# 调用方约定：在需要的地方写成 `from app.application.container import adapter_factory as _f;
# AdapterFactory = _f()`，保持原有名字与调用写法不变。返回的都是无状态类或单例，单进程内安全。
def adapter_factory():
    from app.infrastructure.drive.factory import AdapterFactory

    return AdapterFactory


def base_adapter_cls():
    from app.infrastructure.drive.adapters.base_adapter import BaseCloudDriveAdapter

    return BaseCloudDriveAdapter


def aliyun_adapter_cls():
    from app.infrastructure.drive.adapters.aliyun_adapter import AliyunAdapter

    return AliyunAdapter


def drive_auth_required():
    from app.infrastructure.drive.adapters.drive_auth import DriveAuthRequired

    return DriveAuthRequired


def account_manager_cls():
    from app.infrastructure.drive.account_manager import DatabaseAccountManager

    return DatabaseAccountManager


def task_scheduler():
    from app.infrastructure.scheduling.task_scheduler import task_scheduler_manager

    return task_scheduler_manager


def task_admission_service():
    from app.infrastructure.scheduling.task_admission import task_admission

    return task_admission


def task_executor_cls():
    from app.infrastructure.execution.task_executor import TaskExecutor

    return TaskExecutor


def execution_log_cls():
    from app.infrastructure.execution.execution_log import ExecutionLog

    return ExecutionLog


def magic_rename_cls():
    from app.infrastructure.execution.magic_rename import MagicRename

    return MagicRename


def douban_client_cls():
    from app.infrastructure.http.douban_client import DoubanClient

    return DoubanClient


def tmdb_client_cls():
    from app.infrastructure.http.tmdb_client import TMDBClient

    return TMDBClient


def tmdb_base_language():
    from app.infrastructure.http.tmdb_client import BASE_LANGUAGE

    return BASE_LANGUAGE


def search_plugin_base():
    from app.infrastructure.search.base import BaseSearchPlugin

    return BaseSearchPlugin


def drama_parse_size():
    from app.infrastructure.execution.drama_executor import _parse_size

    return _parse_size


def task_share_store(db):
    from app.infrastructure.drive.task_share import SqlTaskShareStore
    return SqlTaskShareStore(db)


def saved_progress_store(db):
    from app.infrastructure.drive.saved_progress import SqlSavedProgressStore
    return SqlSavedProgressStore(db)


def saved_progress_scanner():
    from app.infrastructure.drive.saved_progress import SavedProgressScanner
    return SavedProgressScanner()


def correct_saved_progress_use_case(db):
    from app.application.use_cases.correct_saved_progress import CorrectSavedProgressUseCase
    return CorrectSavedProgressUseCase(db)


def task_share_gateway(plan):
    from app.infrastructure.drive.task_share import open_task_share_gateway
    return open_task_share_gateway(plan)


def create_task_share_use_case(db):
    from app.application.use_cases.create_task_share import CreateTaskShareUseCase
    return CreateTaskShareUseCase(db)


def task_reads_repo():
    from app.infrastructure.persistence.repositories import task_reads
    return task_reads


def execution_reads_repo():
    from app.infrastructure.persistence.repositories import task_executions
    return task_executions


def task_lead_in_use_case(db):
    from app.application.use_cases.task_lead_in import TaskLeadInUseCase
    return TaskLeadInUseCase(db)


def lead_in_provider():
    from app.infrastructure.drive.lead_in_provider import LeadInProvider
    return LeadInProvider()


def task_run_store(db):
    from app.infrastructure.execution.task_run_stages import SqlTaskRunStore
    return SqlTaskRunStore(db)


def task_execution_engine():
    from app.infrastructure.execution.task_run_stages import PreparedTaskEngine
    return PreparedTaskEngine()


def task_run_notifier(db):
    from app.infrastructure.execution.task_run_notifier import TaskRunNotifier
    return TaskRunNotifier(db)


def run_task_use_case(db, *, notifier=None):
    from app.application.use_cases.run_task import RunTaskUseCase
    return RunTaskUseCase(db, notifier=notifier)


def task_run_notification_batch():
    from app.application.notifications.task_batch import TaskNotificationBatch
    return TaskNotificationBatch(task_run_notifier(None))
