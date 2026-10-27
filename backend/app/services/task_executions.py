from __future__ import annotations

import logging

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.settings import settings
from app.models.task_execution import TaskExecution

logger = logging.getLogger(__name__)

DEFAULT_HISTORY_LIMIT = 20


def history_limit() -> int:
    """每个任务保留的执行记录条数（配置在 settings.task_execution_history_limit）。"""
    try:
        value = int(getattr(settings, "task_execution_history_limit", DEFAULT_HISTORY_LIMIT) or DEFAULT_HISTORY_LIMIT)
    except (TypeError, ValueError):
        value = DEFAULT_HISTORY_LIMIT
    return max(1, value)


def prune_task_executions(db: Session, task_id: int, keep: int | None = None) -> int:
    """只保留该任务最近 ``keep`` 条执行记录，返回删除条数。"""
    limit = history_limit() if keep is None else max(1, int(keep))
    try:
        stale_ids = (
            db.execute(
                select(TaskExecution.id)
                .where(TaskExecution.task_id == int(task_id))
                .order_by(TaskExecution.started_at.desc(), TaskExecution.id.desc())
                .offset(limit)
            )
            .scalars()
            .all()
        )
    except Exception:
        logger.warning("读取执行记录失败 task_id=%s", task_id, exc_info=True)
        return 0
    ids = [int(x) for x in stale_ids if x is not None]
    if not ids:
        return 0
    db.execute(delete(TaskExecution).where(TaskExecution.id.in_(ids)))
    db.flush()
    return len(ids)


def prune_all_task_executions(db: Session, keep: int | None = None) -> int:
    """启动时清理所有任务的旧执行记录，返回删除条数。"""
    try:
        task_ids = db.execute(select(TaskExecution.task_id).distinct()).scalars().all()
    except Exception:
        logger.warning("读取任务执行记录失败", exc_info=True)
        return 0
    total = 0
    for task_id in task_ids:
        if task_id is None:
            continue
        total += prune_task_executions(db, int(task_id), keep)
    if total:
        db.flush()
    return total
