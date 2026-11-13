"""任务执行记录仓储（裁剪历史记录相关查询）。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.infrastructure.persistence.models.task_execution import TaskExecution


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


def list_recent_adapter_snapshots(
    db: Session, *, task_id: int, limit: int
) -> list[Any]:
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


def delete_failed_drama_executions(
    db: Session, *, days: int = 0, before: datetime | None = None
) -> int:
    """删除追剧任务的失败执行记录；days>0 时只删该天数之前的。

    `before` 由应用层清理用例给出时以其为准，保证「删除范围」与「仪表盘清理水位」使用同一个截止时间；
    缺省保留历史行为（UTC now 往前推 days 天）。
    """
    from app.infrastructure.persistence.models.task import Task

    task_ids = select(Task.id).where(Task.task_type == "drama")
    stmt = delete(TaskExecution).where(
        TaskExecution.status == "failed",
        TaskExecution.task_id.in_(task_ids),
    )
    cutoff = before
    if cutoff is None and int(days) > 0:
        cutoff = datetime.utcnow() - timedelta(days=int(days))
    if cutoff is not None:
        stmt = stmt.where(TaskExecution.started_at < cutoff)
    return int(db.execute(stmt).rowcount or 0)


# B2 read projections: never materialize execution log/snapshot ORM columns.
from sqlalchemy import LargeBinary, cast, case, func, literal

_READ_SNAPSHOT_KEYS = (
    "generated_share_check",
    "drive_type",
    "name",
    "active",
    "execution_log",
    "tree_truncated",
)


def _safe_json(column):
    return case((func.json_valid(column), column), else_="{}")


def _snapshot_columns():
    source = _safe_json(TaskExecution.adapter_snapshot)
    return [
        func.substr(func.json_extract(source, "$." + key), 1, 4096).label(
            "snapshot_" + key
        )
        for key in _READ_SNAPSHOT_KEYS
    ]


def _read_columns():
    return [
        TaskExecution.id,
        TaskExecution.task_id,
        TaskExecution.status,
        TaskExecution.started_at,
        TaskExecution.finished_at,
        func.substr(TaskExecution.message, 1, 2048).label("message"),
        TaskExecution.stage,
        *_snapshot_columns(),
    ]


def _summary_mapping(row):
    import json

    result = dict(row)
    snapshot = {}
    for key in _READ_SNAPSHOT_KEYS:
        value = result.pop("snapshot_" + key, None)
        if value is None:
            continue
        if key in ("generated_share_check", "execution_log"):
            try:
                value = json.loads(value)
            except (ValueError, TypeError):
                continue
            if not isinstance(value, dict):
                continue
        if key in ("active", "tree_truncated"):
            value = bool(int(value)) if str(value) in ("0", "1") else False
        snapshot[key] = value
    result["adapter_snapshot"] = snapshot
    return result


def read_page(db, task_id, *, page, page_size):
    condition = TaskExecution.task_id == task_id
    total = db.scalar(select(func.count()).select_from(TaskExecution).where(condition))
    rows = db.execute(
        select(*_read_columns())
        .where(condition)
        .order_by(TaskExecution.started_at.desc(), TaskExecution.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).mappings()
    return {
        "items": [_summary_mapping(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def read_recent_summaries(db, task_ids, limit=3, *, status=None):
    if not task_ids:
        return {}
    ranked = (
        select(
            *_read_columns(),
            func.row_number()
            .over(
                partition_by=TaskExecution.task_id,
                order_by=(TaskExecution.started_at.desc(), TaskExecution.id.desc()),
            )
            .label("_rank"),
        )
        .where(
            TaskExecution.task_id.in_(task_ids),
            *([TaskExecution.status == status] if status else []),
        )
        .subquery()
    )
    rows = db.execute(
        select(ranked)
        .where(ranked.c._rank <= limit)
        .order_by(ranked.c.task_id, ranked.c._rank)
    ).mappings()
    result = {}
    for row in rows:
        item = dict(row)
        item.pop("_rank")
        result.setdefault(item["task_id"], []).append(_summary_mapping(item))
    return result


def read_detail(db, task_id, execution_id, *, log_offset, log_limit):
    log_blob = cast(func.coalesce(TaskExecution.run_log, ""), LargeBinary)
    tree_blob = cast(func.coalesce(TaskExecution.tree_summary, ""), LargeBinary)
    # Four bytes is the minimum requested size, enough for any UTF-8 codepoint.
    columns = [
        *_read_columns(),
        func.length(log_blob).label("_log_bytes"),
        func.substr(
            log_blob, min(log_offset + 1, 9223372036854775807), min(65536, log_limit)
        ).label("_chunk"),
        func.substr(tree_blob, 1, 65536).label("_tree"),
        func.length(tree_blob).label("_tree_bytes"),
    ]
    row = (
        db.execute(
            select(*columns).where(
                TaskExecution.id == execution_id, TaskExecution.task_id == task_id
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    result = _summary_mapping(row)
    raw = result.pop("_chunk") or b""
    # An arbitrary incoming offset can land in a continuation byte: advance to the next boundary.
    leading = 0
    while leading < len(raw) and raw[leading] & 0xC0 == 0x80:
        leading += 1
    limit = min(65536, max(4, log_limit))
    chunk = raw[leading:limit].decode("utf-8", errors="ignore")
    consumed = leading + len(chunk.encode("utf-8"))
    total = result.pop("_log_bytes")
    next_offset = min(total, log_offset + consumed)
    result["run_log"] = chunk
    result["log_next_offset"] = next_offset
    result["log_complete"] = next_offset >= total
    result["tree_summary"] = (result.pop("_tree") or b"").decode(
        "utf-8", errors="ignore"
    )
    tree_bytes = result.pop("_tree_bytes")
    metadata = result["adapter_snapshot"].get("execution_log", {})
    result["log_truncated"] = bool(metadata.get("truncated", False))
    try:
        dropped_bytes = int(metadata.get("dropped_bytes", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        dropped_bytes = 0
    result["log_dropped_bytes"] = max(0, dropped_bytes)
    result["tree_truncated"] = tree_bytes > 65536 or bool(
        result["adapter_snapshot"].get("tree_truncated", False)
    )
    return result
