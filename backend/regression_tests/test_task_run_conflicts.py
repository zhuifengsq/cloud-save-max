import json
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.application.dto.task_run import RunTaskCommand, TaskRunResult
from app.application.use_cases.run_task import RunTaskUseCase, RunPolicy
from app.core.errors import ApiError
from app.infrastructure.persistence.base import Base
from app.infrastructure.persistence.models.task import Task
from app.infrastructure.persistence.models.task_execution import TaskExecution
import app.infrastructure.persistence.models


@pytest.mark.parametrize('edit', ['shareurl', 'account_name', 'savepath'])
def test_concurrent_execution_identity_edit_rejects_stale_progress(tmp_path, edit):
    engine = create_engine('sqlite:///' + str(tmp_path / 'run-race.db'))
    Base.metadata.create_all(engine)
    with Session(engine) as setup:
        task = Task(task_uid='race', task_type='drama', taskname='show',
                    shareurl='https://pan.quark.cn/s/old', savepath='/old', account_name='old',
                    enabled=True, extra_json=json.dumps({'allow_once':True,'user_note':'initial'}))
        setup.add(task); setup.commit(); task_id=task.id
    calls=[]
    class Engine:
        def execute(self, plan, policy, log, recover):
            with Session(engine) as other:
                current=other.get(Task,task_id)
                setattr(current, edit, {'shareurl':'https://pan.quark.cn/s/user', 'account_name':'user', 'savepath':'/user'}[edit])
                current.extra_json=json.dumps({'allow_once':True,'user_note':'user edit'})
                other.commit()
            calls.append('transfer')
            plan.task.shareurl='https://pan.quark.cn/s/recovered'
            plan.task.enabled=False
            plan.task.extra_json=json.dumps({'allow_once':True,'user_note':'initial','saved_latest':{'episode':9}})
            return TaskRunResult(task_id=task_id,status='success')
    class Notify:
        def send(self,*args,**kwargs): calls.append('notify')
    with Session(engine, expire_on_commit=False) as db:
        # Keep the old ORM row cached: the persistence stage must still refresh current data.
        cached=db.get(Task,task_id)
        with pytest.raises(ApiError) as failure:
            RunTaskUseCase(db,engine=Engine(),notifier=Notify()).execute(RunTaskCommand(task_id),RunPolicy.for_trigger('http'))
        assert (failure.value.code,failure.value.http_status)==('TASK_CHANGED',409)
    with Session(engine) as verify:
        current=verify.get(Task,task_id)
        assert current.enabled
        assert json.loads(current.extra_json)=={'allow_once':True,'user_note':'user edit'}
        assert getattr(current,edit)=={'shareurl':'https://pan.quark.cn/s/user','account_name':'user','savepath':'/user'}[edit]
        assert verify.scalars(select(TaskExecution)).all()==[]
    assert calls==['transfer']
    engine.dispose()


def test_recovery_round_does_not_overwrite_manually_selected_source(tmp_path, monkeypatch):
    engine=create_engine('sqlite:///' + str(tmp_path / 'recovery-race.db'))
    Base.metadata.create_all(engine)
    with Session(engine) as setup:
        row=Task(task_uid='recovery-race',task_type='drama',taskname='show',shareurl='https://pan.quark.cn/s/old',savepath='/show',enabled=True,extra_json='{"auto_update_shareurl":true}')
        setup.add(row);setup.commit();task_id=row.id
    calls=[]
    def replace(db,task,**kwargs):
        kwargs['release_read']()
        with Session(engine) as other:
            row=other.get(Task,task_id);row.shareurl='https://pan.quark.cn/s/manual';other.commit()
        calls.append('search')
        task.shareurl='https://pan.quark.cn/s/automatic'
        return {'checked':True,'updated':True,'new_shareurl':task.shareurl}
    monkeypatch.setattr('app.application.drama_share_autoupdate.resolve_drama_shareurl_update',replace)
    class Engine:
        def execute(self,plan,policy,log,recover):
            from app.infrastructure.execution.execution_log import ExecutionLog
            calls.append('transfer')
            recover(plan.task,ExecutionLog(),set())
            raise AssertionError('must stop after conflicting recovery write')
    with Session(engine) as db:
        with pytest.raises(ApiError,match='TASK_CHANGED'):
            RunTaskUseCase(db,engine=Engine()).execute(RunTaskCommand(task_id),RunPolicy.for_trigger('http'))
    with Session(engine) as verify:
        assert verify.get(Task,task_id).shareurl=='https://pan.quark.cn/s/manual'
        assert verify.scalars(select(TaskExecution)).all()==[]
    assert calls==['transfer','search']
    engine.dispose()


def test_unrelated_extra_edits_merge_with_execution_progress(tmp_path):
    engine=create_engine('sqlite:///' + str(tmp_path / 'extra-race.db'))
    Base.metadata.create_all(engine)
    with Session(engine) as setup:
        row=Task(task_uid='extra-race',task_type='generic',taskname='show',shareurl='https://pan.quark.cn/s/old',savepath='/show',enabled=True,extra_json='{"note":"old"}')
        setup.add(row);setup.commit();task_id=row.id
    class Engine:
        def execute(self,plan,policy,log,recover):
            with Session(engine) as other:
                row=other.get(Task,task_id);row.extra_json='{"note":"new","custom":42}';other.commit()
            plan.task.extra_json='{"note":"old","saved_latest":{"episode":9}}'
            return TaskRunResult(task_id=task_id,status='success')
    with Session(engine) as db:
        RunTaskUseCase(db,engine=Engine()).execute(RunTaskCommand(task_id),RunPolicy.for_trigger('http'))
    with Session(engine) as verify:
        assert json.loads(verify.get(Task,task_id).extra_json)=={'note':'new','custom':42,'saved_latest':{'episode':9}}
    engine.dispose()
