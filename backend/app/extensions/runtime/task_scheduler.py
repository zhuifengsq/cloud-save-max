from __future__ import annotations

import os
import time
import logging
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.core.settings import settings
from app.db.session import SessionLocal
from app.models.task import Task
from app.services.notifications.sender import send_runtime
from app.services.notifications.task_notify import DRAMA_NOTIFY_TITLE, build_task_section
from app.services.task_scheduler import get_or_create_task_scheduler_setting
from app.services.tmdb_cache import purge_cold_cache, refresh_expired_cache, refresh_linked_tasks
from app.services.tmdb_cache_scheduler import get_or_create_tmdb_cache_scheduler_setting
from app.services.drive_account_probe_scheduler import get_or_create_drive_account_probe_scheduler_setting
from app.services.drive_accounts import probe_drive_account, sign_in_drive_account
from app.services.audit_log_scheduler import get_or_create_audit_log_scheduler_setting, purge_old_audit_logs
from app.models.drive_account import DriveAccount
from app.extensions.runtime.account_manager import DatabaseAccountManager
from app.extensions.runtime.task_executor import TaskExecutor
from app.core.errors import ApiError


logger = logging.getLogger(__name__)


class TaskSchedulerManager:
    def __init__(self):
        self.scheduler: BackgroundScheduler | None = None

    def start(self) -> None:
        if settings.environment == "test" or os.environ.get("PYTEST_CURRENT_TEST"):
            return
        if self.scheduler is not None:
            return
        self.scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        self.scheduler.start()
        self.reload()

    def shutdown(self) -> None:
        if self.scheduler is None:
            return
        self.scheduler.shutdown(wait=False)
        self.scheduler = None

    def reload(self) -> None:
        if self.scheduler is None:
            return
        with SessionLocal() as db:
            try:
                setting = get_or_create_task_scheduler_setting(db)
                db.commit()
                db.refresh(setting)
                self._apply_setting(setting)
            except OperationalError as e:
                logger.error(f"任务调度配置加载失败: {e}")
                return
            except Exception as e:
                logger.error(f"任务调度配置应用失败: {e}")

            try:
                tmdb_cache_setting = get_or_create_tmdb_cache_scheduler_setting(db)
                db.commit()
                db.refresh(tmdb_cache_setting)
                self._apply_tmdb_cache_setting(tmdb_cache_setting)
            except OperationalError as e:
                logger.error(f"TMDB 缓存调度配置加载失败: {e}")
                return
            except Exception as e:
                logger.error(f"TMDB 缓存调度配置应用失败: {e}")

            try:
                drive_probe_setting = get_or_create_drive_account_probe_scheduler_setting(db)
                db.commit()
                db.refresh(drive_probe_setting)
                self._apply_drive_account_probe_setting(drive_probe_setting)
            except OperationalError as e:
                logger.error(f"驱动账号探测调度配置加载失败: {e}")
                return
            except Exception as e:
                logger.error(f"驱动账号探测调度配置应用失败: {e}")

            try:
                audit_log_setting = get_or_create_audit_log_scheduler_setting(db)
                db.commit()
                db.refresh(audit_log_setting)
                self._apply_audit_log_setting(audit_log_setting)
            except OperationalError as e:
                logger.error(f"审计日志清理调度配置加载失败: {e}")
                return
            except Exception as e:
                logger.error(f"审计日志清理调度配置应用失败: {e}")

    def _apply_setting(self, setting: Any) -> None:
        if self.scheduler is None:
            return
        job_id = "drama_tasks"
        if not bool(setting.enabled):
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
            return
        try:
            trigger = CronTrigger.from_crontab(str(setting.crontab), timezone=str(setting.timezone or "Asia/Shanghai"))
            self.scheduler.add_job(
                run_drama_tasks,
                trigger=trigger,
                id=job_id,
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=60,
            )
        except Exception as e:
            logger.error(f"任务调度 crontab 无效 job_id={job_id} crontab={getattr(setting, 'crontab', '')}: {e}")
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
            return

    def _apply_tmdb_cache_setting(self, setting: Any) -> None:
        if self.scheduler is None:
            return
        job_id = "tmdb_cache_refresh"
        if not bool(getattr(setting, "enabled", False)):
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
            return
        try:
            trigger = CronTrigger.from_crontab(str(setting.crontab), timezone=str(setting.timezone or "Asia/Shanghai"))
            self.scheduler.add_job(
                run_tmdb_cache_refresh,
                trigger=trigger,
                id=job_id,
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=60,
            )
        except Exception as e:
            logger.error(f"TMDB 缓存调度 crontab 无效 job_id={job_id} crontab={getattr(setting, 'crontab', '')}: {e}")
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
            return
        job = self.scheduler.get_job(job_id)


    def _apply_drive_account_probe_setting(self, setting: Any) -> None:
        if self.scheduler is None:
            return
        job_id = "drive_account_probe"
        if not bool(getattr(setting, "enabled", False)):
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
            return
        try:
            trigger = CronTrigger.from_crontab(str(setting.crontab), timezone=str(setting.timezone or "Asia/Shanghai"))
            self.scheduler.add_job(
                run_drive_account_probe,
                trigger=trigger,
                id=job_id,
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=60,
            )
        except Exception as e:
            logger.error(f"驱动账号探测调度 crontab 无效 job_id={job_id} crontab={getattr(setting, 'crontab', '')}: {e}")
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
            return
        job = self.scheduler.get_job(job_id)


    def _apply_audit_log_setting(self, setting: Any) -> None:
        if self.scheduler is None:
            return
        job_id = "audit_log_cleanup"
        if not bool(getattr(setting, "enabled", False)):
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
            return
        try:
            trigger = CronTrigger.from_crontab(str(setting.crontab), timezone=str(setting.timezone or "Asia/Shanghai"))
            self.scheduler.add_job(
                run_audit_log_cleanup,
                trigger=trigger,
                id=job_id,
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=60,
            )
        except Exception as e:
            logger.error(f"审计日志清理调度 crontab 无效 job_id={job_id} crontab={getattr(setting, 'crontab', '')}: {e}")
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
            return
        job = self.scheduler.get_job(job_id)


def run_drama_tasks() -> None:
    task_ids: list[int] = []
    task_payloads: list[dict[str, Any]] = []
    with SessionLocal() as db:
        rows = (
            db.execute(select(Task.id, Task.task_uid, Task.account_name, Task.shareurl).where(Task.enabled.is_(True), Task.task_type == "drama").order_by(Task.id.asc()))
            .all()
        )
        for tid, uid, account_name, shareurl in rows:
            if tid is None:
                continue
            task_ids.append(int(tid))
            task_payloads.append({"task_uid": uid, "account_name": account_name, "shareurl": shareurl})

    account_manager = None
    if task_payloads:
        try:
            with SessionLocal() as adb:
                account_manager = DatabaseAccountManager(adb)
            if account_manager is not None:
                account_manager.init_for_tasks(task_payloads)
        except Exception:
            account_manager = None

    sections: list[str] = []

    for task_id in task_ids:
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            with SessionLocal() as db:
                try:
                    task = db.get(Task, int(task_id))
                    if task is None:
                        break
                    executor = TaskExecutor(db)
                    execution = executor.run_task(
                        task,
                        account_manager=account_manager,
                        init_account_for_task=False,
                    )
                    should_notify = False
                    try:
                        section, should_notify = build_task_section(task, execution)
                        if should_notify and section:
                            sections.append(section)
                    except Exception:
                        pass
                    db.commit()
                    break
                except OperationalError as exc:
                    db.rollback()
                    if attempt < max_attempts and "database is locked" in str(exc).lower():
                        time.sleep(0.2 * attempt)
                        continue
                    logger.warning("追剧任务调度执行异常 task_id=%s err=%s", task_id, str(exc))
                    break
                except Exception as exc:
                    db.rollback()
                    logger.warning("追剧任务调度执行失败 task_id=%s err=%s", task_id, str(exc))
                    break

    with SessionLocal() as db:
        if sections:
            try:
                send_runtime(db, DRAMA_NOTIFY_TITLE, "\n\n".join(sections))
                db.commit()
            except Exception:
                db.rollback()
def run_tmdb_cache_refresh() -> None:
    with SessionLocal() as db:
        setting = get_or_create_tmdb_cache_scheduler_setting(db)
        db.commit()
        db.refresh(setting)

        max_items = int(getattr(setting, "max_items_per_run", 200) or 200)
        only_linked = bool(getattr(setting, "only_refresh_linked_tasks", True))
        retention_days = int(getattr(setting, "retention_days", 60) or 60)

        try:
            if only_linked:
                result = refresh_linked_tasks(db, enabled_only=True, max_items=max_items, force=True)
            else:
                result = refresh_expired_cache(db, max_items=max_items, force=True)
            deleted = purge_cold_cache(db, retention_days=retention_days)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.warning(
                "TMDB 缓存定时刷新执行失败 only_refresh_linked_tasks=%s max_items_per_run=%s retention_days=%s err=%s",
                only_linked,
                max_items,
                retention_days,
                str(e),
            )


def run_drive_account_probe() -> None:
    def _is_sqlite_locked(exc: Exception) -> bool:
        if not isinstance(exc, OperationalError):
            return False
        return "database is locked" in str(exc).lower()

    with SessionLocal() as db:
        setting = get_or_create_drive_account_probe_scheduler_setting(db)
        db.commit()
        db.refresh(setting)
        enabled_only = bool(getattr(setting, "enabled_only", True))
        accounts = (
            db.execute(select(DriveAccount).order_by(DriveAccount.is_default.desc(), DriveAccount.id.asc()))
            .scalars()
            .all()
        )
        account_ids = []
        skipped = 0
        for account in accounts:
            if enabled_only and not bool(getattr(account, "enabled", False)):
                skipped += 1
                continue
            account_id = int(getattr(account, "id", 0) or 0)
            if account_id > 0:
                account_ids.append(account_id)

    ok = 0
    failed = 0

    for account_id in account_ids:
        probed_status: str | None = None
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            with SessionLocal() as db:
                try:
                    account = db.get(DriveAccount, account_id)
                    prev_status = getattr(account, "runtime_status", None) if account is not None else None
                    prev_enabled = bool(getattr(account, "enabled", True)) if account is not None else True
                    probed = probe_drive_account(db, account_id)
                    new_status = getattr(probed, "runtime_status", None)
                    new_enabled = bool(getattr(probed, "enabled", True))
                    last_error = str(getattr(probed, "last_error", "") or "").strip()
                    notify_content = None
                    if (prev_enabled and not new_enabled) or (
                        prev_status == "active" and new_status in {"inactive", "error"} and new_status != prev_status
                    ):
                        lines = [
                            "驱动账号状态异常",
                            "触发: 自动探测",
                            f"账号: {getattr(probed, 'name', '')} (id={account_id})",
                            f"类型: {getattr(probed, 'drive_type', '')}",
                            f"状态: {prev_status or '-'} -> {new_status or '-'}",
                            f"启用: {prev_enabled} -> {new_enabled}",
                        ]
                        if last_error:
                            lines.append(f"错误: {last_error}")
                        notify_content = "\n".join(lines)
                    db.commit()
                    if notify_content:
                        send_runtime(db, DRAMA_NOTIFY_TITLE, notify_content)
                    probed_status = str(new_status or "").strip() or None
                    ok += 1
                    break
                except OperationalError as exc:
                    db.rollback()
                    if attempt < max_attempts and _is_sqlite_locked(exc):
                        time.sleep(0.2 * attempt)
                        continue
                    logger.warning("驱动账号自动探测异常 account_id=%s err=%s", account_id, str(exc))
                    failed += 1
                    break
                except Exception as exc:
                    db.rollback()
                    try:
                        account = db.get(DriveAccount, account_id)
                        prev_status = getattr(account, "runtime_status", None) if account is not None else None
                        prev_enabled = bool(getattr(account, "enabled", True)) if account is not None else True
                        if account is not None:
                            account.runtime_status = "inactive"
                            account.last_error = str(exc)
                            db.commit()
                            if prev_enabled or prev_status == "active":
                                lines = [
                                    "驱动账号状态异常",
                                    "触发: 自动探测",
                                    f"账号: {getattr(account, 'name', '')} (id={account_id})",
                                    f"类型: {getattr(account, 'drive_type', '')}",
                                    f"状态: {prev_status or '-'} -> inactive",
                                    f"启用: {prev_enabled} -> {bool(getattr(account, 'enabled', True))}",
                                    f"错误: {str(exc)}",
                                ]
                                send_runtime(db, DRAMA_NOTIFY_TITLE, "\n".join(lines))
                    except Exception:
                        db.rollback()
                    logger.warning("驱动账号自动探测失败 account_id=%s err=%s", account_id, str(exc))
                    failed += 1
                    break

        if probed_status != "active":
            continue

        max_signin_attempts = 3
        for attempt in range(1, max_signin_attempts + 1):
            with SessionLocal() as sdb:
                try:
                    sign_in_drive_account(sdb, account_id)
                    sdb.commit()
                    break
                except ApiError as exc:
                    sdb.rollback()
                    if exc.code in {"DRIVE_SIGNIN_UNSUPPORTED"}:
                        pass
                    else:
                        logger.warning(
                            "驱动账号自动签到失败 account_id=%s code=%s msg=%s",
                            account_id,
                            exc.code,
                            exc.message,
                        )
                    break
                except OperationalError as exc:
                    sdb.rollback()
                    if attempt < max_signin_attempts and _is_sqlite_locked(exc):
                        time.sleep(0.2 * attempt)
                        continue
                    logger.warning("驱动账号自动签到异常 account_id=%s err=%s", account_id, str(exc))
                    break
                except Exception as exc:
                    sdb.rollback()
                    logger.warning("驱动账号自动签到异常 account_id=%s err=%s", account_id, str(exc))
                    break


def run_audit_log_cleanup() -> None:
    with SessionLocal() as db:
        try:
            setting = get_or_create_audit_log_scheduler_setting(db)
            db.commit()
            db.refresh(setting)
            retention_days = int(getattr(setting, "retention_days", 30) or 30)
            deleted = purge_old_audit_logs(db, retention_days=retention_days)
            db.commit()
        except Exception:
            db.rollback()


task_scheduler_manager = TaskSchedulerManager()
