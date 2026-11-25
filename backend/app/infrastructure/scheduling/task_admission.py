"""Process-local admission; deploy one process/replica (docs/architecture/adr/0003-single-process-deployment-assumption.md)."""
from __future__ import annotations

from collections.abc import Callable
from threading import Condition, Lock, Thread
import time

from app.core.errors import ApiError
from app.core.settings import settings


class TaskAdmission:
    """Reserve a slot before creating a worker or starting any task side effects."""

    def __init__(self, max_active: int):
        self._max_active = max_active
        self._lock = Lock()
        self._cond = Condition(self._lock)
        self._active = 0
        self._task_ids: set[int] = set()

    @property
    def max_active(self) -> int:
        return self._max_active

    def reserve(self, task_id: int | None) -> TaskSlot:
        with self._lock:
            if task_id is not None and task_id in self._task_ids:
                raise ApiError("TASK_ALREADY_RUNNING", "该任务正在执行，请等待本次执行结束", 409)
            if self._active >= self._max_active:
                raise ApiError("TASK_CAPACITY_EXCEEDED", "正在执行的任务过多，请稍后再试", 429)
            self._active += 1
            if task_id is not None:
                self._task_ids.add(task_id)
        return TaskSlot(self, task_id)

    def wait_for_capacity(self, task_id: int | None, timeout: float = 0.0) -> TaskSlot:
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._lock:
            while True:
                if task_id is not None and task_id in self._task_ids:
                    raise ApiError("TASK_ALREADY_RUNNING", "该任务正在执行，请等待本次执行结束", 409)
                if self._active < self._max_active:
                    self._active += 1
                    if task_id is not None:
                        self._task_ids.add(task_id)
                    return TaskSlot(self, task_id)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ApiError("TASK_CAPACITY_EXCEEDED", "正在执行的任务过多，请稍后再试", 429)
                self._cond.wait(timeout=remaining)


class TaskSlot:
    def __init__(self, admission: TaskAdmission, task_id: int | None):
        self._admission = admission
        self._task_id = task_id
        self._released = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()

    def release(self) -> None:
        admission = self._admission
        with admission._lock:
            if self._released:
                return
            self._released = True
            admission._active -= 1
            if self._task_id is not None:
                admission._task_ids.remove(self._task_id)
            admission._cond.notify_all()

    def start_worker(self, target: Callable[[], None]) -> None:
        def run():
            with self:
                target()

        try:
            Thread(target=run, daemon=True).start()
        except BaseException:
            self.release()
            raise


task_admission = TaskAdmission(max_active=settings.drama_task_max_active)
