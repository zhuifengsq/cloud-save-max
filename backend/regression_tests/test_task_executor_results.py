"""Direct executor result/persistence behavior beyond prepared-use-case callers."""
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.infrastructure.execution.task_executor import TaskExecutor
from app.infrastructure.persistence.base import Base
from app.infrastructure.persistence.models.task import Task
from app.infrastructure.persistence.models.task_execution import TaskExecution
import app.infrastructure.persistence.models


@pytest.mark.parametrize('persist', [False, True])
@pytest.mark.parametrize('available', [False, True])
def test_failure_results_keep_metadata_and_do_not_replay_io(tmp_path, persist, available):
    engine = create_engine('sqlite:///' + (tmp_path / 'executor.db').as_posix())
    Base.metadata.create_all(engine)
    calls = []
    def token(*args):
        calls.append('token')
        return {'status': 500, 'message': 'fake failure'}
    adapter = SimpleNamespace(is_active=True, nickname='fake', DRIVE_TYPE='quark',
        extract_url=lambda _: ('fake', '', '', []), get_stoken=token)
    manager = SimpleNamespace(init_for_tasks=lambda _: None,
        get_adapter_for_task=lambda _: adapter if available else None)
    with Session(engine) as db:
        task = Task(task_uid='failure', task_type='generic', taskname='fake',
                    shareurl='https://pan.quark.cn/s/fake', savepath='/fake', enabled=True,
                    addition_json='{}', extra_json='{}')
        db.add(task); db.commit()
        result = TaskExecutor(db).run_task(task, persist_execution=persist,
            account_manager=manager, prepared_data=TaskExecutor._task_to_dict(task))
        assert result.status == 'failed'
        assert result.task_id == task.id
        assert result.started_at is not None and result.finished_at >= result.started_at
        assert result.stage == ('execute_generic' if available else 'end')
        assert calls == (['token'] if available else [])
        assert 'execution_log' in result.adapter_snapshot
        assert db.scalar(select(func.count()).select_from(TaskExecution)) == int(persist)
        assert bool(result.id) == persist
    engine.dispose()
