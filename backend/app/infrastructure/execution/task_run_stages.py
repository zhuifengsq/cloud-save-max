"""Infrastructure stages. No execution commit is owned here."""
import json
from dataclasses import asdict
from datetime import datetime
from typing import Any
from sqlalchemy.orm import Session, sessionmaker
from app.domain.task_run import ExecutionLogPort, RecoveryCallback, RunPolicyPort, TaskExecutionState
from sqlalchemy import select, update
from app.core.errors import ApiError
from app.infrastructure.persistence.models.task import Task
from app.application.dto.task_run import TaskRunPlan, TaskRunResult, TaskRunState, RunTaskCommand
from app.application.tasks import get_task
from app.application.magic_regex import get_enabled_magic_regex_map
from app.application.generated_share_check import SNAPSHOT_KEY, prepare_generated_share_check, probe_generated_share_check
from app.application.task_executions import prune_task_executions
from app.infrastructure.persistence.models.task_execution import TaskExecution
from app.infrastructure.persistence.repositories.drama_transfers import DramaTransferStore
from app.infrastructure.drive.account_manager import load_accounts_payload, DatabaseAccountManager
from app.infrastructure.drive.factory import AdapterFactory
from app.infrastructure.execution.drama_executor import check_drama_schedule_skip
from app.infrastructure.execution.execution_log import ExecutionLog
from app.infrastructure.execution.task_executor import TaskExecutor
from app.infrastructure.drive.lifecycle import scoped_adapters


def scope_accounts_payload(
    payload: dict[str, Any],
    *,
    task_data: dict[str, Any] | None = None,
    share_check: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Filter accounts snapshot down to accounts needed by this task and its outbound share check."""
    accounts = payload.get("accounts")
    if not isinstance(accounts, list):
        return payload

    needed_names: set[str] = set()
    needed_drives: set[str] = set()
    record = share_check.get("record") if isinstance(share_check, dict) else None
    for cfg in (task_data, record if isinstance(record, dict) else None):
        if not isinstance(cfg, dict):
            continue
        name = str(cfg.get("account_name") or "").strip()
        if name:
            needed_names.add(name)
            continue
        url = str(cfg.get("shareurl") or cfg.get("share_url") or "").strip()
        drive = AdapterFactory.get_drive_type_by_url(url) if url else None
        if not drive:
            return payload
        needed_drives.add(drive)

    if not needed_names and not needed_drives:
        return payload

    filtered = [
        acc
        for acc in accounts
        if isinstance(acc, dict)
        and (
            str(acc.get("name") or "").strip() in needed_names
            or str(acc.get("drive_type") or "").strip() in needed_drives
        )
    ]
    return {**payload, "accounts": filtered}

class SqlTaskRunStore:
    def __init__(self, db: Session) -> None:
        self.db = db

    def prepare(self, command: RunTaskCommand) -> TaskRunPlan:
        if command.preview is None:
            row = get_task(self.db, command.task_id)
            values = {column.name: getattr(row, column.name) for column in row.__table__.columns}
        else:
            row = None
            values = dict(command.preview, id=0, enabled=True)
        task = TaskRunState(**values)
        reservation = command.scheduled_identity
        if reservation is not None and not reservation.matches(task):
            raise ApiError("TASK_CHANGED", "调度后任务配置已变更，留待下轮执行", 409)
        if not task.enabled:
            from app.core.errors import bad_request
            raise bad_request('TASK_DISABLED', '任务已禁用')
        self.baseline = {key:getattr(task,key) for key in ('extra_json','shareurl','enabled','account_name','savepath')}
        data = TaskExecutor._task_to_dict(task)
        if command.schedule_now is not None:
            data['schedule_now'] = command.schedule_now
        if row is not None and task.task_type == 'drama':
            data['drama_transfer_store'] = DramaTransferStore(sessionmaker(bind=self.db.get_bind()), task.id)
        data['magic_regex'] = get_enabled_magic_regex_map(self.db)
        data.update(tmdb_configured=False, tmdb_update_weekdays=[], tmdb_episode_weekdays=[])
        # TMDB owns its separate cache-write unit and can close its read snapshot before HTTP.
        if data.get('tmdb_id') and data.get('tmdb_media_type') == 'tv':
            from app.application.tmdb_cache import get_tmdb_detail_cached
            configured, _, weekdays, episodes, _ = get_tmdb_detail_cached(
                self.db, media_type='tv', tmdb_id=int(data['tmdb_id']), close_read_tx=True)
            data.update(tmdb_configured=configured, tmdb_update_weekdays=weekdays,
                        tmdb_episode_weekdays=episodes)
        check = prepare_generated_share_check(self.db, task) if row is not None and task.task_type == 'drama' else None
        from app.infrastructure.persistence.models.notification_setting import NotificationSetting
        from app.application.notifications.settings import load_notification_config
        setting = self.db.get(NotificationSetting, 1)
        notification_config = load_notification_config(setting) if setting is not None else {}
        return TaskRunPlan(task, {'task_data':data, 'share_check':check,
                                 'reserved_account':reservation.account_name if reservation and reservation.named_account_reserved else None,
                                 'accounts':load_accounts_payload(self.db), 'notification_config':notification_config})

    def save_task(self, task: TaskExecutionState) -> None:
        # Refresh even when a caller keeps ORM identity-map entries across commits.
        current = self.db.execute(select(Task).where(Task.id == task.id)
            .execution_options(populate_existing=True)).scalars().first()
        original = self.baseline
        identity = ('shareurl', 'account_name', 'savepath')
        if current is None or any(getattr(current, key) != original[key] for key in identity):
            raise ApiError('TASK_CHANGED', '任务配置已变更，请重新执行', 409)
        changes: dict[str, Any] = {}
        if task.extra_json != original['extra_json']:
            before = json.loads(original['extra_json'] or '{}')
            after = json.loads(task.extra_json or '{}')
            merged = json.loads(current.extra_json or '{}')
            for key in before.keys() - after.keys():
                merged.pop(key, None)
            merged.update({key: value for key, value in after.items()
                           if key not in before or value != before[key]})
            changes['extra_json'] = json.dumps(merged, ensure_ascii=False)
        for key in ('shareurl', 'enabled'):
            if getattr(task, key) != original[key]:
                if getattr(current, key) != original[key]:
                    raise ApiError('TASK_CHANGED', '任务配置已变更，请重新执行', 409)
                changes[key] = getattr(task, key)
        # Guard the UPDATE as well as the read, including the exact extra value we
        # merged, so a writer between those operations cannot lose unrelated keys.
        conditions = [Task.id == task.id, Task.extra_json == current.extra_json]
        conditions.extend(getattr(Task, key) == original[key] for key in identity)
        if 'enabled' in changes:
            conditions.append(Task.enabled == original['enabled'])
        updated = self.db.execute(update(Task).where(*conditions)
            .values(changes or {'updated_at': Task.updated_at})
            .execution_options(synchronize_session=False))
        if updated.rowcount != 1:
            raise ApiError('TASK_CHANGED', '任务配置已变更，请重新执行', 409)
        self.db.expire(current)
        self.baseline = {key: getattr(task, key) for key in original}

    def save(self, plan: TaskRunPlan, result: TaskRunResult) -> None:
        self.save_task(plan.task)
        transfer_store = plan.prepared.get('task_data', {}).get('drama_transfer_store')
        if transfer_store is not None and result.status == 'success':
            transfer_store.acknowledge_renames(self.db)
        values = asdict(result)
        values.pop('id')
        values['adapter_snapshot'] = json.dumps(result.adapter_snapshot, ensure_ascii=False)
        row = TaskExecution(**values)
        self.db.add(row)
        self.db.flush()
        result.id = row.id
        prune_task_executions(self.db, plan.task.id)

class PreparedTaskEngine:
    @scoped_adapters
    def execute(self, plan: TaskRunPlan, policy: RunPolicyPort, log: ExecutionLogPort | None, recover: RecoveryCallback) -> TaskRunResult:
        task_data = plan.prepared["task_data"]
        share_check = plan.prepared.get("share_check")
        if task_data.get("task_type") == "drama" and not policy.manual_run:
            skip_reason = check_drama_schedule_skip(task_data, manual_run=False, log=None)
            if skip_reason is not None:
                run_log: ExecutionLogPort = log or ExecutionLog()
                run_log.set_stage("start")
                run_log.section("程序开始")
                run_log.line(f"执行时间: {run_log.started_at.strftime('%Y-%m-%d %H:%M:%S')}")
                run_log.line(f"任务名称: {plan.task.taskname}")
                run_log.line(f"分享链接: {plan.task.shareurl}")
                run_log.line(f"保存路径: {plan.task.savepath}")
                run_log.line("")
                check: dict[str, Any] | None = None
                if share_check is not None:
                    scoped_check_accounts = scope_accounts_payload(
                        plan.prepared["accounts"], task_data=None, share_check=share_check
                    )
                    check_manager = DatabaseAccountManager.from_payload(scoped_check_accounts)
                    check = probe_generated_share_check(
                        share_check, check_manager, always_notify=policy.share_check_always_notify
                    )
                    if check:
                        run_log.section("检测已生成的分享链接")
                        run_log.line(str(check["message"]))
                check_drama_schedule_skip(task_data, manual_run=False, log=run_log)
                run_log.set_stage("validate_schedule")
                run_log.section("程序结束")
                finished_at = datetime.now()
                run_log.line("状态: skipped")
                run_log.line(f"运行时长: {(finished_at - run_log.started_at).total_seconds():.2f}s")
                snapshot: dict[str, Any] = {"execution_log": run_log.metadata()}
                if check:
                    snapshot[SNAPSHOT_KEY] = check
                return TaskRunResult(
                    id=0,
                    task_id=int(getattr(plan.task, "id", 0) or 0),
                    status="skipped",
                    started_at=run_log.started_at,
                    finished_at=finished_at,
                    tree_summary="",
                    message=skip_reason,
                    stage="validate_schedule",
                    run_log=run_log.render(),
                    adapter_snapshot=snapshot,
                )

        scoped_accounts = scope_accounts_payload(
            plan.prepared["accounts"], task_data=task_data, share_check=share_check
        )
        manager = DatabaseAccountManager.from_payload(scoped_accounts)
        reserved_account = plan.prepared.get("reserved_account")
        if reserved_account:
            selected = manager.get_adapter_for_task(task_data)
            if getattr(selected, 'account_name', None) != reserved_account:
                raise ApiError("TASK_ACCOUNT_CHANGED", "本轮指定账号不可用，留待下轮重新调度", 409)
        check = probe_generated_share_check(share_check, manager,
                                           always_notify=policy.share_check_always_notify)
        executor = TaskExecutor(None)
        raw = executor.run_task(plan.task, log=log, persist_execution=False,
                                account_manager=manager, manual_run=policy.manual_run,
                                prepared_data=task_data,
                                _generated_share_check=check, recover=recover)
        return TaskRunResult(**{field:getattr(raw, field) for field in
            ('id','task_id','status','started_at','finished_at','tree_summary','message','stage','run_log')},
            adapter_snapshot=json.loads(raw.adapter_snapshot or '{}'))
