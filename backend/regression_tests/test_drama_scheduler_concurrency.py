from threading import Event, Thread, get_ident
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.application.notifications.task_batch import TaskNotificationBatch
from app.core.errors import ApiError
from app.infrastructure.persistence.base import Base
from app.infrastructure.persistence.models.drive_account import DriveAccount
from app.infrastructure.persistence.models.task import Task
from app.infrastructure.persistence.models.task_execution import TaskExecution
from app.infrastructure.execution import task_run_stages
from app.infrastructure.scheduling import task_scheduler
from app.infrastructure.scheduling.task_admission import TaskAdmission


@pytest.mark.parametrize("edit", [
    {"savepath": "/already-active"}, {"shareurl": "https://pan.baidu.com/s/new"}, {"account_name": "other"},
    {"task_type": "generic"},
])
def test_scheduler_defers_task_reconfigured_after_round_scan(tmp_path, monkeypatch, caplog, edit):
    engine = create_engine("sqlite:///" + (tmp_path / "changed.db").as_posix())
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions.begin() as db:
        db.add(Task(id=1, task_uid="queued", taskname="queued", enabled=True, task_type="drama",
                    shareurl="https://pan.quark.cn/s/original", account_name="A", savepath="/old"))
    monkeypatch.setattr(task_scheduler, "SessionLocal", sessions)
    original_order = task_scheduler._scheduled_drama_order

    def edit_after_scan():
        order = original_order()
        with sessions.begin() as db:
            row = db.get(Task, 1)
            for field, value in edit.items():
                setattr(row, field, value)
        return order

    entered = []
    def cloud_engine(*args, **kwargs):
        entered.append(True)
        raise AssertionError("配置已变更，本轮不应进入网盘执行")

    monkeypatch.setattr(task_scheduler, "_scheduled_drama_order", edit_after_scan)
    monkeypatch.setattr(task_run_stages.PreparedTaskEngine, "execute", cloud_engine)
    task_scheduler.run_drama_tasks()
    assert entered == []
    assert "task_id=1" in caplog.text
    engine.dispose()


def test_two_mock_drives_transfer_while_first_confirmation_waits(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///" + (tmp_path / "adapters.db").as_posix())
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions.begin() as db:
        for i, name in enumerate(("A", "B"), 1):
            db.add(DriveAccount(name=name, drive_type="quark", enabled=True))
            db.add(Task(id=i, task_uid=name, taskname=name, account_name=name, shareurl=f"https://example.test/{i}",
                        savepath=f"/{name}", enabled=True, task_type="drama", extra_json='{"allow_once":true}'))

    confirming, second_saved, release = Event(), Event(), Event()
    calls = []

    class Adapter:
        DRIVE_TYPE = "mock"
        is_active = True

        def __init__(self, name):
            self.account_name = self.nickname = name
            self.saved = False

        def extract_url(self, url):
            return self.account_name, "", "", []

        def get_stoken(self, *args):
            return {"data": {"stoken": "token"}}

        def get_fids(self, paths):
            return [{"file_path": paths[0], "fid": "dest"}]

        def get_detail(self, *args):
            return {"code": 0, "data": {"list": [{"fid": "source", "file_name": "Show.S01E01.mkv"}]}}

        def save_file(self, *args):
            if self.account_name == "B":
                assert confirming.wait(5)
                second_saved.set()
            calls.append((self.account_name, "save"))
            self.saved = True
            return {"code": 0, "data": {"save_as_top_fids": ["saved"]}}

        def ls_dir(self, *args, **kwargs):
            if self.saved and self.account_name == "A":
                calls.append(("A", "confirm"))
                confirming.set()
                assert release.wait(5)
            rows = [{"fid": "saved", "file_name": "Show.S01E01.mkv"}] if self.saved else []
            return {"code": 0, "data": {"list": rows}}

    adapters = {name: Adapter(name) for name in ("A", "B")}
    manager = SimpleNamespace(init_for_tasks=lambda tasks: None, get_adapter_for_task=lambda data: adapters[data["account_name"]])
    monkeypatch.setattr(task_run_stages.DatabaseAccountManager, "from_payload", lambda *args: manager)
    monkeypatch.setattr(task_scheduler, "SessionLocal", sessions)
    monkeypatch.setattr(task_scheduler, "task_admission", TaskAdmission(2))
    sink = SimpleNamespace(send=lambda *args, **kwargs: None)
    monkeypatch.setattr(task_scheduler, "task_run_notification_batch", lambda: TaskNotificationBatch(sink))
    coordinator = Thread(target=task_scheduler.run_drama_tasks)
    coordinator.start()
    try:
        assert confirming.wait(5)
        assert second_saved.wait(2)
    finally:
        release.set()
        coordinator.join(5)
    assert not coordinator.is_alive()
    assert calls == [("A", "save"), ("A", "confirm"), ("B", "save")]
    with sessions() as db:
        assert db.scalars(select(TaskExecution.status).order_by(TaskExecution.task_id)).all() == ["success", "success"]
        assert db.scalars(select(Task.enabled)).all() == [False, False]
    engine.dispose()


@pytest.mark.parametrize("registered, should_probe", [(True, False), (False, True)])
def test_scheduler_named_adapter_construction_failure_respects_reservation(tmp_path, monkeypatch, registered, should_probe):
    from app.infrastructure.drive.factory import AdapterFactory

    engine = create_engine("sqlite:///" + (tmp_path / "fallback.db").as_posix())
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions.begin() as db:
        db.add(DriveAccount(name="available", drive_type="quark", enabled=True))
        if registered:
            db.add(DriveAccount(name="requested", drive_type="quark", enabled=True))
        db.add(Task(id=1, task_uid="fallback", taskname="fallback", enabled=True, task_type="drama",
                    shareurl="https://pan.quark.cn/s/test", account_name="requested", savepath="/show"))
    monkeypatch.setattr(task_scheduler, "SessionLocal", sessions)
    fallback = SimpleNamespace(account_name="available", DRIVE_TYPE="quark", is_active=True)
    monkeypatch.setattr(AdapterFactory, "create_adapter",
                        lambda *args, account_name, **kwargs: fallback if account_name == "available" else None)
    probed = []
    def probe(plan, manager, **kwargs):
        selected = manager.get_adapter_for_task({"account_name": "requested", "shareurl": "https://pan.quark.cn/s/test"})
        probed.append(selected.account_name)
        raise RuntimeError("stop at generated-share probe")
    monkeypatch.setattr(task_run_stages, "probe_generated_share_check", probe)
    task_scheduler.run_drama_tasks()
    assert probed == (["available"] if should_probe else [])
    engine.dispose()


def test_automatic_account_selection_orders_possible_fallbacks(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///" + (tmp_path / "auto.db").as_posix())
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions.begin() as db:
        db.add(DriveAccount(name="Q", drive_type="quark", enabled=True))
        db.add(DriveAccount(name="B", drive_type="baidu", enabled=True))
        q_url, b_url = "https://pan.quark.cn/s/q", "https://pan.baidu.com/s/b"
        for i, (account, url) in enumerate((("Q", q_url), ("missing", q_url), (None, b_url),
                                             ("B", b_url), (None, "unknown")), 1):
            db.add(Task(id=i, task_uid=str(i), taskname=str(i), enabled=True, task_type="drama",
                        shareurl=url, account_name=account, savepath=f"/{i}"))
    monkeypatch.setattr(task_scheduler, "SessionLocal", sessions)
    order, _identities = task_scheduler._scheduled_drama_order()
    assert set(order.get_ready()) == {1, 3}
    order.done(3)
    assert order.get_ready() == (4,)
    order.done(1, 4)
    assert order.get_ready() == (2,)
    order.done(2)
    assert order.get_ready() == (5,)
    engine.dispose()


def test_scheduler_runs_independent_account_during_transfer_confirmation(tmp_path, monkeypatch, caplog):
    engine = create_engine("sqlite:///" + (tmp_path / "concurrency.db").as_posix())
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions.begin() as db:
        for account in ("A", "B", "C"):
            db.add(DriveAccount(name=account, drive_type="quark", enabled=True))
        for i, (account, path) in enumerate((("A", "/one"), ("A", "/two"), ("B", "/one/"), ("C", "/three")), 1):
            db.add(Task(id=i, task_uid=str(i), taskname=str(i), task_type="drama", account_name=account,
                        shareurl="https://pan.quark.cn/s/id", savepath=path, enabled=True))

    class ScopedSession(Session):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.owner = get_ident()

    admission = TaskAdmission(max_active=2)
    monkeypatch.setattr(task_scheduler, "SessionLocal", sessionmaker(bind=engine, class_=ScopedSession))
    monkeypatch.setattr(task_scheduler, "task_admission", admission)
    delivered = []
    sink = SimpleNamespace(send=lambda title, message, **kw: delivered.append((get_ident(), message)))
    monkeypatch.setattr(task_scheduler, "task_run_notification_batch", lambda: TaskNotificationBatch(sink))
    confirming, independent, release = Event(), Event(), Event()
    calls, worker_sessions = [], []

    def executor(db, *, notifier):
        def execute(command, policy):
            i = command.task_id
            worker_sessions.append((db, get_ident()))
            calls.append(("start", i))
            if i == 1:
                confirming.set()
                assert release.wait(5)
            elif i == 4:
                independent.set()
            elif i == 2:
                raise RuntimeError("simulated cloud error")
            calls.append(("done", i))
            notifier.send("追剧", str(i))
        return SimpleNamespace(execute=execute)

    monkeypatch.setattr(task_scheduler, "run_task_use_case", executor)
    thread = Thread(target=task_scheduler.run_drama_tasks)
    thread.start()
    try:
        assert confirming.wait(3)
        overlapped = independent.wait(1)
        before_release = calls.copy()
        with pytest.raises(ApiError) as exc:
            admission.reserve(1)
        assert exc.value.code == "TASK_ALREADY_RUNNING"
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert overlapped, calls
    assert ("start", 2) not in before_release and ("start", 3) not in before_release
    assert sorted(i for event, i in calls if event == "start") == [1, 2, 3, 4]
    assert all(session.owner == owner for session, owner in worker_sessions)
    assert len({id(s) for s, _ in worker_sessions}) == 4
    assert delivered == [(thread.ident, "1\n\n3\n\n4")]
    assert "task_id=2" in caplog.text
    caplog.set_level("INFO", logger=task_scheduler.__name__)
    caplog.clear()
    with admission.reserve(None), admission.reserve(None):
        task_scheduler.run_drama_tasks()
    capacity_skips = [record.getMessage() for record in caplog.records if "TASK_CAPACITY_EXCEEDED" in record.getMessage()]
    assert all(any(f"task_id={i} " in message for message in capacity_skips) for i in range(1, 5))
    assert sorted(i for event, i in calls if event == "start") == [1, 2, 3, 4]
    assert len(delivered) == 1
    engine.dispose()
