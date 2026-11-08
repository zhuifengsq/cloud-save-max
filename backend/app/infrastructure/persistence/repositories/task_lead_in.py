"""追剧引流记录仓储（task_lead_in 表；S5 从 drama_lead_in 下沉）。"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.persistence.models.task_lead_in import TaskLeadInRecord


def get_record(db: Session, task_id: int, drive_type: str | None = None) -> Any:
    """取任务最近一条引流记录（可按网盘类型过滤）。"""
    statement = select(TaskLeadInRecord).where(TaskLeadInRecord.task_id == int(task_id))
    if drive_type:
        statement = statement.where(TaskLeadInRecord.drive_type == str(drive_type))
    return (
        db.execute(statement.order_by(TaskLeadInRecord.updated_at.desc(), TaskLeadInRecord.id.desc()))
        .scalars()
        .first()
    )


def upsert_record(db: Session, **fields: Any) -> Any:
    """按 (task_id, drive_type) 幂等写入引流记录（字段截断规则与旧实现一致）。"""
    task_id = int(fields["task_id"])
    drive_type = str(fields["drive_type"])
    record = (
        db.execute(
            select(TaskLeadInRecord).where(
                TaskLeadInRecord.task_id == task_id,
                TaskLeadInRecord.drive_type == drive_type,
            )
        )
        .scalars()
        .first()
    )
    if record is None:
        record = TaskLeadInRecord(task_id=task_id, drive_type=drive_type)
        db.add(record)
    record.account_name = str(fields.get("account_name") or "")[:128]
    record.dir_path = str(fields.get("dir_path") or "")[:512]
    record.material_name = str(fields.get("material_name") or "")[:512]
    record.material_fid = str(fields.get("material_fid") or "")[:256]
    record.status = str(fields.get("status") or "")
    record.message = str(fields.get("message") or "")[:512]
    record.config_version = int(fields.get("config_version") or 0)
    db.flush()
    return record


def list_by_task_ids(db: Session, task_ids: list[int]) -> list[Any]:
    """批量取多个任务的引流记录（列表页一次 IN 查询，避免 N+1）。"""
    if not task_ids:
        return []
    return list(
        db.execute(select(TaskLeadInRecord).where(TaskLeadInRecord.task_id.in_(task_ids)))
        .scalars()
        .all()
    )
