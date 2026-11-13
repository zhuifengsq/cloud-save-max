from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.application.use_cases.run_task import RunTaskUseCase, RunPolicy
from app.application.dto.task_run import RunTaskCommand, TaskRunResult


def test_commit_failure_does_not_replay_or_notify():
    with Session(create_engine('sqlite://')) as db:
        calls=[]
        plan=SimpleNamespace(task=SimpleNamespace(id=1,task_type='drama', taskname='test',savepath='/'), prepared={})
        class Store:
            def prepare(self, command):
                db.execute(__import__('sqlalchemy').text('select 1'))
                return plan
            def save(self, plan, result):
                calls.append('save')
                raise RuntimeError('commit failed')
        class Engine:
            def execute(self, plan, policy, log, recover):
                assert not db.in_transaction()
                calls.append('network')
                return TaskRunResult(id=0,task_id=1,status='success')
        class Notify:
            def send(self,*a,**kw): calls.append('notify')
        uc=RunTaskUseCase(db, store=Store(), engine=Engine(), notifier=Notify())
        with pytest.raises(RuntimeError,match='commit failed'):
            uc.execute(RunTaskCommand(1),RunPolicy.for_trigger('http'))
        assert calls==['network','save']

@pytest.mark.parametrize('trigger,manual,persist,force', [
    ('http',True,True,True),('sse',True,True,True),('scheduler',False,True,False),
    ('telegram',True,True,True),('preview',True,False,False)])
def test_policy_truth_table(trigger,manual,persist,force):
    policy=RunPolicy.for_trigger(trigger)
    assert (policy.manual_run,policy.persist_execution,policy.share_check_always_notify)==(manual,persist,force)


@pytest.mark.parametrize("fail_commit", [False, True])
def test_real_sqlite_engine_network_outside_transaction_and_notification_after_commit(monkeypatch, fail_commit):
    from app.infrastructure.persistence.base import Base
    import app.infrastructure.persistence.models
    from app.infrastructure.persistence.models.task import Task
    from app.infrastructure.persistence.models.task_execution import TaskExecution
    from app.infrastructure.execution import task_run_stages as stages
    from app.infrastructure.execution.task_executor import TaskExecutor
    from app.application import audit
    engine=create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        task=Task(task_uid='phase',task_type='drama',taskname='show',shareurl='https://pan.quark.cn/s/demo',savepath='/show',enabled=True,addition_json='{}',extra_json='{}')
        from app.application.task_share_links import save_task_share_link
        save_task_share_link(task, share_url='https://pan.quark.cn/s/public')
        db.add(task);db.commit(); task_id=task.id
        calls=[]
        def probe(record, manager):
            assert not db.in_transaction()
            calls.append('probe')
            return 'pending', '分享正在审核中'
        monkeypatch.setattr('app.application.generated_share_check._probe', probe)
        def transfer():
            assert not db.in_transaction()
            calls.append('transfer')
        class Manager:
            @classmethod
            def from_payload(cls,payload):
                assert not db.in_transaction();calls.append('init');return cls()
            def init_for_tasks(self,tasks): assert not db.in_transaction()
            def get_adapter_for_task(self,*a,**k): return SimpleNamespace(is_active=True,nickname='test',DRIVE_TYPE='quark', transfer=transfer)
        class Drama:
            def __init__(self,**kw): self.saved_latest=None; self.adapter=kw['adapter']
            def execute(self):
                from treelib import Tree
                self.adapter.transfer()
                tree=Tree();tree.create_node('show','root');tree.create_node('episode','file',parent='root',data={'file_name_re':'01.mp4'})
                return tree
        monkeypatch.setattr(stages,'DatabaseAccountManager',Manager)
        monkeypatch.setattr('app.infrastructure.execution.task_executor.DramaTaskExecutor',Drama)
        class Notify:
            def send(self,*a,**kw):
                assert not db.in_transaction()
                assert db.query(TaskExecution).count()==1
                calls.append('notify')
                raise RuntimeError('optional notification failure')
        from sqlalchemy import event
        def before_commit(session):
            if fail_commit and calls == ['init','probe','transfer']:
                raise RuntimeError('local commit failed')
        event.listen(db, 'before_commit', before_commit)
        if fail_commit:
            with pytest.raises(RuntimeError, match='local commit failed'):
                RunTaskUseCase(db,notifier=Notify()).execute(RunTaskCommand(task_id),RunPolicy.for_trigger('http'))
            assert calls == ['init','probe','transfer']
            assert db.query(TaskExecution).count() == 0
            return
        result=RunTaskUseCase(db,notifier=Notify()).execute(RunTaskCommand(task_id),RunPolicy.for_trigger('http'))
        assert result.status=='success' and result.id>0
        assert result.adapter_snapshot['new_files']==['01.mp4']
        assert result.adapter_snapshot['generated_share_check']['status']=='pending'
        assert not result.adapter_snapshot['generated_share_check']['notify']
        assert calls==['init','probe','transfer','notify']


def test_scheduler_batch_sends_committed_sections_once():
    from app.application.notifications.task_batch import TaskNotificationBatch
    calls=[]
    class Notifier:
        def send(self,title,message,**kwargs): calls.append((title,message))
    batch=TaskNotificationBatch(Notifier())
    batch.send('tasks','first')
    batch.send('tasks','second')
    assert not calls
    batch.flush();batch.flush()
    assert calls == [('tasks','first\n\nsecond')]
