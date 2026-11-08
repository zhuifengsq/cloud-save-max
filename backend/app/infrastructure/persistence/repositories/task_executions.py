"""任务执行记录仓储（裁剪历史记录相关查询）。"""
from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.task_execution import TaskExecution


def list_stale_execution_ids(db: Session, *, task_id: int, limit: int) -> list[Any]:
    rows = (
        db.execute(
            select(TaskExecution.id)
            .where(TaskExecution.task_id == int(task_id))
            .order_by(TaskExecution.started_at.desc(), TaskExecution.id.desc())
            .offset(limit)
        )
        .scalars()
        .all()
    )
    return list(rows)


def delete_executions(db: Session, *, ids: list[int]) -> int:
    db.execute(delete(TaskExecution).where(TaskExecution.id.in_(ids)))
    db.flush()
    return len(ids)


def list_distinct_task_ids(db: Session) -> list[Any]:
    return list(db.execute(select(TaskExecution.task_id).distinct()).scalars().all())


def list_recent_adapter_snapshots(db: Session, *, task_id: int, limit: int) -> list[Any]:
    with db.no_autoflush:
        return list(
            db.scalars(
                select(TaskExecution.adapter_snapshot)
                .where(TaskExecution.task_id == int(task_id))
                .order_by(TaskExecution.id.desc())
                .limit(int(limit))
            ).all()
        )


def get_latest_for_task(db: Session, task_id: int):
    return (
        db.execute(
            select(TaskExecution)
            .where(TaskExecution.task_id == int(task_id))
            .order_by(TaskExecution.id.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )


def list_desc(db: Session, task_id: int, *, limit: int):
    return (
        db.execute(
            select(TaskExecution)
            .where(TaskExecution.task_id == int(task_id))
            .order_by(TaskExecution.id.desc())
            .limit(int(limit))
        )
        .scalars()
        .all()
    )
