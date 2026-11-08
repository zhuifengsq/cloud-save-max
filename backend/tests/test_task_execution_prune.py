from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.task import Task
from app.models.task_execution import TaskExecution
from app.services.task_executions import DEFAULT_HISTORY_LIMIT, history_limit, prune_all_task_executions, prune_task_executions


class TaskExecutionPruneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine, tables=[Task.__table__, TaskExecution.__table__])
        self.db = Session(self.engine)
        self.task = Task(task_uid="t1", task_type="drama", taskname="测试", shareurl="https://pan.quark.cn/s/x", savepath="/tv/x")
        self.db.add(self.task)
        self.db.flush()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def _add_executions(self, count: int, base: datetime | None = None) -> None:
        base = base or datetime(2026, 1, 1, 0, 0, 0)
        for index in range(count):
            self.db.add(
                TaskExecution(
                    task_id=int(self.task.id),
                    status="success",
                    started_at=base + timedelta(minutes=index),
                    run_log="log",
                )
            )
        self.db.flush()

    def test_keeps_only_latest_records(self) -> None:
        self._add_executions(25)

        removed = prune_task_executions(self.db, int(self.task.id), keep=20)

        self.assertEqual(removed, 5)
        rows = (
            self.db.execute(
                select(TaskExecution.started_at)
                .where(TaskExecution.task_id == int(self.task.id))
                .order_by(TaskExecution.started_at)
            )
            .scalars()
            .all()
        )
        self.assertEqual(len(rows), 20)
        self.assertEqual(rows[0], datetime(2026, 1, 1, 0, 5, 0))

    def test_uses_default_limit_when_not_given(self) -> None:
        self.assertEqual(history_limit(), DEFAULT_HISTORY_LIMIT)
        self._add_executions(DEFAULT_HISTORY_LIMIT + 3)

        removed = prune_task_executions(self.db, int(self.task.id))

        self.assertEqual(removed, 3)

    def test_noop_when_under_limit(self) -> None:
        self._add_executions(3)
        self.assertEqual(prune_task_executions(self.db, int(self.task.id)), 0)
        count = len(
            self.db.execute(select(TaskExecution.id).where(TaskExecution.task_id == int(self.task.id))).scalars().all()
        )
        self.assertEqual(count, 3)

    def test_does_not_touch_other_tasks(self) -> None:
        other = Task(task_uid="t2", task_type="drama", taskname="另一个", shareurl="https://pan.quark.cn/s/y", savepath="/tv/y")
        self.db.add(other)
        self.db.flush()
        self._add_executions(5)
        for index in range(5):
            self.db.add(
                TaskExecution(task_id=int(other.id), status="success", started_at=datetime(2026, 1, 1) + timedelta(minutes=index), run_log="log")
            )
        self.db.flush()

        prune_task_executions(self.db, int(self.task.id), keep=2)

        other_count = len(
            self.db.execute(select(TaskExecution.id).where(TaskExecution.task_id == int(other.id))).scalars().all()
        )
        self.assertEqual(other_count, 5)


    def test_prune_all_covers_every_task(self) -> None:
        other = Task(task_uid="t3", task_type="drama", taskname="第三个", shareurl="https://pan.quark.cn/s/z", savepath="/tv/z")
        self.db.add(other)
        self.db.flush()
        self._add_executions(23)
        for index in range(22):
            self.db.add(
                TaskExecution(
                    task_id=int(other.id),
                    status="success",
                    started_at=datetime(2026, 2, 1) + timedelta(minutes=index),
                    run_log="log",
                )
            )
        self.db.flush()

        removed = prune_all_task_executions(self.db)

        self.assertEqual(removed, 5)
        for task_id in (int(self.task.id), int(other.id)):
            count = len(
                self.db.execute(select(TaskExecution.id).where(TaskExecution.task_id == task_id)).scalars().all()
            )
            self.assertEqual(count, 20)


if __name__ == "__main__":
    unittest.main()
