import importlib
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.infrastructure.persistence.base import Base
import app.infrastructure.persistence.models
from app.infrastructure.persistence.models.task import Task
from app.infrastructure.persistence.models.drive_account import DriveAccount
from app.infrastructure.persistence.models.audit_log import AuditLog


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'share.db').as_posix()}")
    Base.metadata.create_all(engine)  # isolated SQL transaction test
    with Session(engine) as session:
        session.add(Task(id=1, task_uid='one', taskname='剧', task_type='drama',
                         shareurl='https://pan.quark.cn/s/a', savepath='/剧', account_name='账号',
                         extra_json='{"keep":true}'))
        session.add(DriveAccount(name='账号', drive_type='quark', cookie='private', enabled=True,
                                 runtime_status='active', is_default=True))
        session.commit()
        yield session
    engine.dispose()


def load():
    return importlib.import_module('app.application.use_cases.create_task_share')


def gateway(db, calls):
    def open_gateway(plan):
        assert not db.in_transaction(), 'network must start after releasing read transaction'
        assert plan.account_name == '账号' and plan.savepath == '/剧'
        def create(path, title):
            assert not db.in_transaction()
            calls.append((path, title))
            return SimpleNamespace(url='https://pan.quark.cn/s/new', share_id='new', title=title,
                                   passcode='1234', expires_in_days=7, supported=True)
        return SimpleNamespace(capabilities=SimpleNamespace(supports_share=True), create_share_link=create)
    return open_gateway


def test_use_case_releases_transaction_before_network_and_persists_audit(db):
    module = load()
    calls = []
    use_case = module.CreateTaskShareUseCase(db, gateway_factory=gateway(db, calls))
    result = use_case.execute(module.CreateTaskShareCommand(task_id=1, actor_user_id=None))
    assert calls == [('/剧', '剧')]
    assert result.share_url.endswith('/new') and result.account_name == '账号'
    extra = json.loads(db.get(Task, 1).extra_json)
    assert extra['keep'] and extra['share_link']['passcode'] == '1234'
    assert db.scalars(select(AuditLog).where(AuditLog.action == 'task.share.create')).first() is not None


def test_failed_commit_rolls_back_link_and_never_replays_network(db, monkeypatch, caplog):
    module = load()
    calls = []
    def fail_commit():
        raise RuntimeError('commit unavailable token=private-credential')
    # Read transaction release is performed before the only business commit.
    original = db.commit
    count = 0
    def commit():
        nonlocal count
        count += 1
        if count == 1:
            return original()
        return fail_commit()
    monkeypatch.setattr(db, 'commit', commit)
    with pytest.raises(RuntimeError, match='commit unavailable'):
        module.CreateTaskShareUseCase(db, gateway_factory=gateway(db, calls)).execute(
            module.CreateTaskShareCommand(task_id=1, actor_user_id=None))
    assert len(calls) == 1
    assert 'share_link' not in json.loads(db.get(Task, 1).extra_json)
    assert db.scalars(select(AuditLog)).all() == []
    assert 'task_id=1' in caplog.text and 'drive_type=quark' in caplog.text
    assert 'error_type=RuntimeError' in caplog.text
    assert 'private-credential' not in caplog.text


def test_missing_task_has_no_external_side_effect(db):
    module = load()
    calls = []
    with pytest.raises(Exception) as caught:
        module.CreateTaskShareUseCase(db, gateway_factory=gateway(db, calls)).execute(
            module.CreateTaskShareCommand(task_id=999, actor_user_id=None))
    assert caught.value.code == 'TASK_NOT_FOUND'
    assert not calls
