"""Infrastructure stages. No execution commit is owned here."""
import json
from dataclasses import asdict
from sqlalchemy import select, update
from app.core.errors import ApiError
from app.infrastructure.persistence.models.task import Task
from app.application.dto.task_run import TaskRunPlan, TaskRunResult, TaskRunState, RunTaskCommand
from app.application.tasks import get_task
from app.application.magic_regex import get_enabled_magic_regex_map
from app.application.generated_share_check import prepare_generated_share_check, probe_generated_share_check
from app.application.task_executions import prune_task_executions
from app.infrastructure.persistence.models.task_execution import TaskExecution
from app.infrastructure.drive.account_manager import load_accounts_payload, DatabaseAccountManager
from app.infrastructure.execution.task_executor import TaskExecutor

class SqlTaskRunStore:
    def __init__(self, db):
        self.db = db

    def prepare(self, command: RunTaskCommand) -> TaskRunPlan:
        row = get_task(self.db, command.task_id) if command.preview is None else None
        values = ({column.name: getattr(row, column.name) for column in row.__table__.columns}
                  if row is not None else dict(command.preview, id=0, enabled=True))
        task = TaskRunState(**values)
        if not task.enabled:
            from app.core.errors import bad_request
            raise bad_request('TASK_DISABLED', '任务已禁用')
        self.baseline = {key:getattr(task,key) for key in ('extra_json','shareurl','enabled','account_name','savepath')}
        data = TaskExecutor._task_to_dict(task)
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
                                 'accounts':load_accounts_payload(self.db), 'notification_config':notification_config})

    def save_task(self, task: TaskRunState) -> None:
        # Refresh even when a caller keeps ORM identity-map entries across commits.
        current = self.db.execute(select(Task).where(Task.id == task.id)
            .execution_options(populate_existing=True)).scalars().first()
        original = self.baseline
        identity = ('shareurl', 'account_name', 'savepath')
        if current is None or any(getattr(current, key) != original[key] for key in identity):
            raise ApiError('TASK_CHANGED', '任务配置已变更，请重新执行', 409)
        changes = {}
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
        values = asdict(result)
        values.pop('id')
        values['adapter_snapshot'] = json.dumps(result.adapter_snapshot, ensure_ascii=False)
        row = TaskExecution(**values)
        self.db.add(row)
        self.db.flush()
        result.id = row.id
        prune_task_executions(self.db, plan.task.id)

class PreparedTaskEngine:
    def execute(self, plan: TaskRunPlan, policy, log, recover) -> TaskRunResult:
        manager = DatabaseAccountManager.from_payload(plan.prepared['accounts'])
        check = probe_generated_share_check(plan.prepared['share_check'], manager,
                                           always_notify=policy.share_check_always_notify)
        executor = TaskExecutor(None)
        raw = executor.run_task(plan.task, log=log, persist_execution=False,
                                account_manager=manager, manual_run=policy.manual_run,
                                prepared_data=plan.prepared['task_data'],
                                _generated_share_check=check, recover=recover)
        return TaskRunResult(**{field:getattr(raw, field) for field in
            ('id','task_id','status','started_at','finished_at','tree_summary','message','stage','run_log')},
            adapter_snapshot=json.loads(raw.adapter_snapshot or '{}'))
