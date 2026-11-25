"""任务仓储（S3 逐步把 services 里的 select() 搬到这里）。"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import case, func, select, update
from sqlalchemy.orm import Session, noload

from app.application.repository_records import TaskRecord
from app.infrastructure.persistence.models.task import Task


def iter_drama_tasks_without_executions(db: Session):
    """遍历全部追剧任务，且不加载 executions（清历史换源日志用）。"""
    return db.scalars(select(Task).options(noload(Task.executions)).where(Task.task_type == 'drama'))


def list_drama_tasks(db: Session):
    """全部追剧任务（按 id 升序），不加载 executions。"""
    return (
        db.execute(select(Task).options(noload(Task.executions)).where(Task.task_type == "drama").order_by(Task.id.asc()))
        .scalars()
        .all()
    )


def list_drama_execution_rows(db: Session, *, since):
    """仪表盘用：只取概览需要的列，旧版成功记录按需读取结果树。"""
    from app.infrastructure.persistence.models.task_execution import TaskExecution

    snapshot = TaskExecution.adapter_snapshot
    safe_snapshot = case((func.json_valid(snapshot), snapshot), else_="{}")
    legacy_tree = case((
        (TaskExecution.status == "success")
        & (func.coalesce(func.json_type(safe_snapshot, "$.new_files"), "") != "array"),
        TaskExecution.tree_summary,
    ))
    return db.execute(
        select(
            TaskExecution.status,
            TaskExecution.started_at,
            TaskExecution.finished_at,
            TaskExecution.task_id,
            TaskExecution.stage,
            TaskExecution.message,
            snapshot,
            Task.taskname,
            legacy_tree,
        )
        .join(Task, Task.id == TaskExecution.task_id)
        .where(Task.task_type == "drama", TaskExecution.started_at >= since)
        .order_by(TaskExecution.started_at.desc(), TaskExecution.id.desc())
    ).all()


def list_tasks(db: Session):
    """全部任务（id 倒序），不加载 executions。"""
    return db.execute(select(Task).options(noload(Task.executions)).order_by(Task.id.desc())).scalars().all()


def list_tasks_recent_executions(db: Session, *, limit: int = 3):
    """列表页：任务 + 每任务最近 N 条执行记录（显式装配，绝不触发全量懒加载）。"""
    from app.infrastructure.persistence.models.task_execution import TaskExecution

    tasks = list_tasks(db)
    if not tasks:
        return []

    task_ids = [int(getattr(t, "id", 0) or 0) for t in tasks]
    task_ids = [tid for tid in task_ids if tid > 0]
    if not task_ids or limit <= 0:
        for t in tasks:
            t.executions = []
        return tasks

    rn = (
        select(
            TaskExecution.id.label("id"),
            TaskExecution.task_id.label("task_id"),
            func.row_number()
            .over(partition_by=TaskExecution.task_id, order_by=TaskExecution.started_at.desc())
            .label("rn"),
        )
        .where(TaskExecution.task_id.in_(task_ids))
        .subquery()
    )
    recent_ids = db.execute(select(rn.c.id).where(rn.c.rn <= limit)).scalars().all()

    exec_map: dict[int, list[Any]] = {tid: [] for tid in task_ids}
    if recent_ids:
        executions = (
            db.execute(
                select(TaskExecution)
                .where(TaskExecution.id.in_(recent_ids))
                .order_by(TaskExecution.task_id.asc(), TaskExecution.started_at.desc())
            )
            .scalars()
            .all()
        )
        for ex in executions:
            tid = int(getattr(ex, "task_id", 0) or 0)
            if tid in exec_map:
                exec_map[tid].append(ex)

    for t in tasks:
        tid = int(getattr(t, "id", 0) or 0)
        t.executions = exec_map.get(tid, [])

    return tasks


def get_task_row(db: Session, task_id: int):
    return db.execute(select(Task).where(Task.id == task_id)).scalars().first()


def guard_task_edit(db: Session, task: TaskRecord) -> bool:
    # 检查选中的快照并取得写锁，随后在同一事务中合并配置与执行状态。
    result = db.execute(
        update(Task)
        .where(
            Task.id == task.id, Task.enabled == task.enabled, Task.extra_json == task.extra_json,
            Task.shareurl == task.shareurl, Task.savepath == task.savepath, Task.account_name == task.account_name,
        )
        .values(updated_at=Task.updated_at)
        .execution_options(synchronize_session=False),
    )
    return result.rowcount == 1


def list_recent_executions(db: Session, task_id: int, *, limit: int = 3):
    from app.infrastructure.persistence.models.task_execution import TaskExecution

    if limit <= 0:
        return []
    return (
        db.execute(
            select(TaskExecution)
            .where(TaskExecution.task_id == int(task_id))
            .order_by(TaskExecution.started_at.desc(), TaskExecution.id.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )


def create_task_row(
    db: Session,
    *,
    task_uid: str,
    task_type: str,
    taskname: str,
    shareurl: str,
    savepath: str,
    pattern: str | None,
    replace: str | None,
    ignore_extension: bool,
    account_name: str | None,
    tmdb_id: int | None,
    tmdb_media_type: str | None,
    enabled: bool,
    addition_json: str,
    extra_json: str,
):
    task = Task(
        task_uid=task_uid,
        task_type=task_type,
        taskname=taskname,
        shareurl=shareurl,
        savepath=savepath,
        pattern=pattern,
        replace=replace,
        ignore_extension=ignore_extension,
        account_name=account_name,
        tmdb_id=tmdb_id,
        tmdb_media_type=tmdb_media_type,
        enabled=enabled,
        addition_json=addition_json,
        extra_json=extra_json,
    )
    db.add(task)
    db.flush()
    return task


def delete_task_row(db: Session, task) -> None:
    db.delete(task)
    db.flush()


def count_tasks(db: Session) -> int:
    from sqlalchemy import func

    return int(db.execute(select(func.count(Task.id))).scalar_one() or 0)


def count_enabled_tasks(db: Session) -> int:
    from sqlalchemy import func

    return int(db.execute(select(func.count(Task.id)).where(Task.enabled.is_(True))).scalar_one() or 0)


def get_first_by_shareurls(db: Session, urls: list[str]):
    candidates = [str(x) for x in (urls or []) if str(x)]
    if not candidates:
        return None
    return db.execute(select(Task).where(Task.shareurl.in_(candidates)).order_by(Task.id.asc())).scalars().first()

def build_task_row(**kwargs):
    """构造一个未持久化的 Task（预览试跑用；调用方设置 id=0 表示不落库）。"""
    return Task(**kwargs)


def _parse_json_dict(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _to_positive_int(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def stop_completed_drama_tasks(db: Session, *, language: str, poster_language: str) -> dict:
    """仅停用 TMDB 已完结/已取消、已追平最新一集且无未完成转存断点的追剧任务。"""
    from sqlalchemy import and_, func, update as sa_update

    from app.domain.tmdb import is_saved_latest_caught_up, resolve_tmdb_latest_aired_episode
    from app.infrastructure.persistence.models.drama_transfer_checkpoint import DramaTransferCheckpoint
    from app.infrastructure.persistence.models.tmdb_media_cache import TMDBMediaCache

    checked = int(
        db.execute(
            select(func.count(Task.id)).where(
                Task.task_type == "drama",
                Task.enabled.is_(True),
                Task.tmdb_id.is_not(None),
                Task.tmdb_media_type == "tv",
            )
        ).scalar_one()
        or 0
    )
    ended_statuses = ["Ended", "Canceled"]
    active_checkpoint_task_ids = {
        int(tid)
        for tid in db.execute(select(DramaTransferCheckpoint.task_id)).scalars().all()
        if tid is not None
    }
    rows = db.execute(
        select(Task.id, Task.extra_json, TMDBMediaCache.payload_json)
        .join(
            TMDBMediaCache,
            and_(
                TMDBMediaCache.media_type == Task.tmdb_media_type,
                TMDBMediaCache.tmdb_id == Task.tmdb_id,
                TMDBMediaCache.language == language,
                TMDBMediaCache.poster_language == poster_language,
            ),
        )
        .where(
            Task.task_type == "drama",
            Task.enabled.is_(True),
            Task.tmdb_id.is_not(None),
            Task.tmdb_media_type == "tv",
            TMDBMediaCache.status.in_(ended_statuses),
        )
        .order_by(Task.id.desc())
    ).all()

    matched_ids: list[int] = []
    for task_id, extra_json, payload_json in rows:
        tid = int(task_id)
        if tid in active_checkpoint_task_ids:
            continue
        details = _parse_json_dict(payload_json)
        tmdb_season, tmdb_episode, _ = resolve_tmdb_latest_aired_episode(details)
        if tmdb_season is None or tmdb_episode is None:
            continue
        extra = _parse_json_dict(extra_json)
        saved_latest = extra.get("saved_latest") if isinstance(extra.get("saved_latest"), dict) else {}
        saved_season = _to_positive_int(saved_latest.get("season"))
        saved_episode = _to_positive_int(saved_latest.get("episode"))
        if not is_saved_latest_caught_up(
            tmdb_season=tmdb_season,
            tmdb_episode=tmdb_episode,
            saved_season=saved_season,
            saved_episode=saved_episode,
        ):
            continue
        matched_ids.append(tid)

    stopped = 0
    if matched_ids:
        db.execute(sa_update(Task).where(Task.id.in_(matched_ids)).values(enabled=False))
        stopped = len(matched_ids)
    return {"checked": checked, "matched_ids": matched_ids, "stopped": stopped}
