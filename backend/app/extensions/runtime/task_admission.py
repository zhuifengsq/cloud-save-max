from __future__ import annotations

from collections.abc import Callable
from threading import Lock, Thread

from app.core.errors import ApiError


class TaskAdmission:
    """Reserve a slot before creating a worker or starting any task side effects."""

    def __init__(self, max_active: int):
        self._max_active = max_active
        self._lock = Lock()
        self._active = 0
        self._task_ids: set[int] = set()

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

    def start_worker(self, target: Callable[[], None]) -> None:
        def run():
            with self:
                target()

        try:
            Thread(target=run, daemon=True).start()
        except BaseException:
            self.release()
            raise


task_admission = TaskAdmission(max_active=4)
