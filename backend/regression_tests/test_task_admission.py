import asyncio
from contextlib import nullcontext
from threading import Event, Thread
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError
from starlette.requests import Request

from app.api.routes import tasks, tasks_stream
from app.api.routes.tasks_stream import _SseQueue
from app.core.errors import ApiError
from app.infrastructure.scheduling import task_scheduler
from app.infrastructure.scheduling.task_admission import TaskAdmission
from app.application.telegram_bot import handlers


def test_slots_reject_duplicates_and_previews_when_full_then_release_after_failure():
    admission = TaskAdmission(max_active=2)
    with admission.reserve(17), admission.reserve(None):
        with pytest.raises(ApiError) as duplicate:
            admission.reserve(17)
        assert duplicate.value.http_status == 409
        with pytest.raises(ApiError) as full:
            admission.reserve(None)
        assert full.value.http_status == 429
    with pytest.raises(TimeoutError), admission.reserve(17):
        raise TimeoutError
    with admission.reserve(17):
        pass


def test_background_worker_holds_slot_until_it_finishes():
    admission = TaskAdmission(max_active=1)
    active = Event()
    finish = Event()
    slot = admission.reserve(17)

    def work():
        active.set()
        finish.wait(2)

    slot.start_worker(work)
    try:
        assert active.wait(1)
        with pytest.raises(ApiError) as duplicate:
            admission.reserve(17)
        assert duplicate.value.code == "TASK_ALREADY_RUNNING"
    finally:
        finish.set()


def test_sse_disconnect_keeps_shared_slot_and_blocks_other_entries(monkeypatch):
    admission = TaskAdmission(max_active=1)
    for module in (tasks_stream, tasks, task_scheduler, handlers):
        monkeypatch.setattr(module, "task_admission", admission)

    started = Event()
    finish = Event()
    effects = []
    task = SimpleNamespace(id=17, taskname="测试", task_type="generic")
    request = Request({"type": "http", "headers": []})
    user = SimpleNamespace(user=SimpleNamespace(id=1))

    class FakeDB:
        def commit(self): pass
        def refresh(self, row): pass
        def execute(self, stmt):
            return SimpleNamespace(all=lambda: [(17, "uid", "", "")])

    class Executor:
        def __init__(self, db): pass
        def run_task(self, task, log=None, **kwargs):
            effects.append(task.id)
            started.set()
            assert finish.wait(2)
            return SimpleNamespace(status="success", message="ok")

    async def prepare(fn):
        return fn()

    monkeypatch.setattr(tasks_stream, "run_in_threadpool", prepare)
    monkeypatch.setattr(tasks_stream, "SessionLocal", lambda: nullcontext(FakeDB()))
    monkeypatch.setattr(tasks_stream, "get_task", lambda db, task_id: task)
    monkeypatch.setattr(tasks_stream.audit, "write_audit_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(tasks_stream, "TaskExecutor", Executor)
    monkeypatch.setattr(tasks_stream, "execution_out", lambda row: SimpleNamespace(model_dump=lambda **kw: {}))
    monkeypatch.setattr(task_scheduler, "SessionLocal", lambda: nullcontext(FakeDB()))
    monkeypatch.setattr(task_scheduler, "DatabaseAccountManager", lambda db: None)

    async def scenario():
        response = await tasks_stream.post_run_task_stream(request, 17, user)
        assert b"event: init" in await anext(response.body_iterator)
        assert started.wait(1)
        await response.body_iterator.aclose()
        with pytest.raises(ApiError, match="TASK_ALREADY_RUNNING"):
            tasks.post_run_task(request, 17, user, FakeDB())
        task_scheduler.run_drama_tasks()
        telegram_messages = []
        bot = SimpleNamespace(_edit_or_send=lambda chat, text, **kwargs: telegram_messages.append(text))
        handlers.TelegramBotHandler._run_async(bot, 1, 1, 18, "pending", lambda db: effects.append(18), "done")
        assert telegram_messages == ["正在执行的任务过多，请稍后再试"]
        assert effects == [17]
        finish.set()
        async with asyncio.timeout(1):
            while True:
                try:
                    with admission.reserve(17):
                        break
                except ApiError:
                    await asyncio.sleep(0.001)

    try:
        asyncio.run(scenario())
    finally:
        finish.set()


def test_scheduler_does_not_repeat_cloud_effect_after_a_database_lock(monkeypatch):
    effects = []
    task = SimpleNamespace(id=17)

    class FakeDB:
        def execute(self, stmt):
            return SimpleNamespace(all=lambda: [(17, "uid", "", "")])

        def get(self, model, task_id):
            return task

        def rollback(self):
            pass

    class Executor:
        def __init__(self, db):
            pass

        def run_task(self, task, **kwargs):
            effects.append(task.id)
            raise OperationalError(None, None, RuntimeError("database is locked"))

    monkeypatch.setattr(task_scheduler, "SessionLocal", lambda: nullcontext(FakeDB()))
    monkeypatch.setattr(task_scheduler, "DatabaseAccountManager", lambda db: None)
    monkeypatch.setattr(task_scheduler, "TaskExecutor", Executor)
    monkeypatch.setattr(task_scheduler, "task_admission", TaskAdmission(max_active=1))
    task_scheduler.run_drama_tasks()
    assert effects == [17]


def test_slow_sse_client_keeps_terminal_event_without_accumulating_logs():
    async def scenario():
        queue = _SseQueue()

        def emit():
            for index in range(2048):
                queue.put(("log", str(index)))
            queue.put(("done", {"status": "success"}))
            queue.put(("done_sentinel", None))

        thread = Thread(target=emit)
        thread.start()
        thread.join()
        items = []
        while True:
            item = await queue.get(0.1)
            assert item is not None
            items.append(item)
            if item[0] == "done_sentinel":
                break
        assert len(items) <= 258
        assert items[-2] == ("done", {"status": "success"})
        queue.stop()
        queue.put(("log", "disconnected"))
        assert await queue.get(0.001) is None

    asyncio.run(scenario())
