"""应用层装配入口（composition）——唯一允许接触 infrastructure 实现的位置。

约束（见 docs/architecture-evolution-plan.md §3.1）：
- 延迟 import 只发生在本文件，且都是函数内 import；分层门禁把这类边记为
  deferred_imports（可见、可预算），不会把业务模块标成 application→infrastructure 违规。
- 单进程假设：这里返回的是无状态模块/单例，不做跨进程共享。
"""
from __future__ import annotations

from contextlib import contextmanager

from types import ModuleType


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


@contextmanager
def db_session():
    """显式开启一个会话（供确需自管会话的服务/扩展边界使用，单进程无 DI）。"""
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def tmdb_media_cache_repo() -> ModuleType:
    """TMDB 媒体缓存仓储。"""
    from app.infrastructure.persistence.repositories import tmdb_media_cache

    return tmdb_media_cache
