"""Real execute/store/engine/drama scheduling with fake drive IO only."""
from datetime import datetime
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session

from app.application.dto.task_run import RunTaskCommand
from app.application.use_cases.run_task import RunPolicy
from app.application.container import run_task_use_case as RunTaskUseCase
from app.domain.drive_types import SavedFile, TransferResult
from app.infrastructure.persistence.base import Base
from app.infrastructure.persistence.models.task import Task
from app.infrastructure.persistence.models.task_execution import TaskExecution


@pytest.mark.parametrize('trigger', ['http', 'sse', 'telegram', 'scheduler', 'preview'])
@pytest.mark.parametrize('share_status', ['valid', 'invalid'])
def test_execute_off_day_transfer_persistence_and_exact_notification(tmp_path, monkeypatch, trigger, share_status):
    from app.infrastructure.execution import task_run_stages as stages, drama_executor
    from app.application.task_share_links import save_task_share_link
    import app.infrastructure.persistence.models
    class FixedDateTime(datetime):
        @classmethod
        def now(cls):
            return cls(2026, 11, 16, 12)  # Monday; task permits Tuesday only.
    monkeypatch.setattr(drama_executor, 'datetime', FixedDateTime)
    engine = create_engine('sqlite:///' + (tmp_path / 'execute.db').as_posix())
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        row = Task(task_uid='trigger', task_type='drama', taskname='show', enabled=True,
                   shareurl='https://pan.quark.cn/s/fake', savepath='/show',
                   addition_json='{}', extra_json=json.dumps({'runweek': [2]}))
        save_task_share_link(row, share_url='https://pan.quark.cn/s/public')
        db.add(row)
        db.commit()
        task_id = row.id
        calls, notices = [], []
        class Strategy:
            def transfer(self, adapter, request, retry):
                assert not db.in_transaction()
                calls.append('transfer')
                return TransferResult('completed', files=(SavedFile('file', 'saved', 'episode.mkv'),), confirmed=True)
        adapter = SimpleNamespace(is_active=True, nickname='fake', DRIVE_TYPE='quark',
            savepath_fid={}, TRANSFER_STRATEGY=Strategy(),
            extract_url=lambda _: ('fake', '', '', []),
            get_stoken=lambda *args: {'code': 0, 'data': {'stoken': 'fake'}},
            get_fids=lambda paths: [{'file_path': paths[0], 'fid': 'dest'}],
            ls_dir=lambda *args, **kwargs: {'code': 0, 'data': {'list': []}},
            get_detail=lambda *args: {
                'code': 0,
                'data': {'list': [{'fid': 'file', 'file_name': 'episode.mkv', 'size': 100}]},
                'metadata': {'_total': 1},
            })
        class Manager:
            @classmethod
            def from_payload(cls, payload):
                assert not db.in_transaction()
                return cls()
            def init_for_tasks(self, tasks):
                assert not db.in_transaction()
            def get_adapter_for_task(self, *args, **kwargs):
                return adapter
        monkeypatch.setattr(stages, 'DatabaseAccountManager', Manager)
        monkeypatch.setattr('app.application.generated_share_check._probe', lambda *_: (share_status, 'fake share check'))
        class Notify:
            def send(self, title, message, **kwargs):
                assert not db.in_transaction()
                notices.append(message)
        command = RunTaskCommand(task_id)
        if trigger == 'preview':
            command = RunTaskCommand(0, preview={'task_type': 'drama', 'taskname': 'show',
                'shareurl': row.shareurl, 'savepath': row.savepath, 'addition_json': '{}', 'extra_json': row.extra_json})
        result = RunTaskUseCase(db, notifier=Notify()).execute(command, RunPolicy.for_trigger(trigger))
        assert result.status == ('skipped' if trigger == 'scheduler' else 'success'), result.message
        assert calls == ([] if trigger == 'scheduler' else ['transfer'])
        assert db.scalar(select(func.count()).select_from(TaskExecution)) == (0 if trigger == 'preview' else 1)
        expected_notify = trigger in {'http', 'sse'} or (share_status == 'invalid' and trigger in {'telegram', 'scheduler'})
        assert len(notices) == int(expected_notify)
        if notices:
            assert ('episode.mkv' in notices[0]) == (trigger in {'http', 'sse'})
            assert ('分享链接已失效' in notices[0]) == (share_status == 'invalid')
    engine.dispose()

