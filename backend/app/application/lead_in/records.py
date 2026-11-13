from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.application.container import task_lead_in_repo
from app.application.audit import write_audit_log
from app.application.drive_accounts import normalize_api_datetime

logger = logging.getLogger(__name__)

import app.application.lead_in.materials as lead_materials


def store_result(
    db: Session,
    *,
    task_id: int,
    drive_type: str,
    account_name: str,
    dir_path: str,
    material_name: str,
    material_fid: str,
    status: str,
    message: str,
    config_version: int,
    actor_user_id: int | None,
    ip: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    status = status if status in lead_materials._ACTIVE_STATUSES else "failed"
    record: Any | None = None
    try:
        record = _upsert_record(
            db,
            task_id=task_id,
            drive_type=drive_type,
            account_name=account_name,
            dir_path=dir_path,
            material_name=material_name,
            material_fid=material_fid,
            status=status,
            message=message,
            config_version=config_version,
        )
        detail = json.dumps(
            {
                "drive_type": drive_type,
                "account_name": account_name,
                "dir_path": dir_path,
                "material_name": material_name,
                "status": status,
                "message": message,
            },
            ensure_ascii=False,
        )[:1024]
        write_audit_log(
            db,
            actor_user_id=actor_user_id,
            action="task.lead_in_add",
            target_type="task",
            target_id=str(task_id),
            ip=ip,
            user_agent=user_agent,
            success=status in ("added", "exists"),
            detail=detail,
        )
        db.flush()
    except Exception:
        db.rollback()
        lead_materials.logger.warning(
            "保存引流记录失败: task_id=%s drive_type=%s", task_id, drive_type
        )
        raise

    return {
        "status": status,
        "message": str(message or ""),
        "added_at": (
            normalize_api_datetime(record.updated_at)
            if record is not None and status == "added"
            else None
        ),
        "record_id": int(record.id) if record is not None else 0,
        "material": {"name": material_name, "fid": material_fid},
        "directory": {"path": dir_path, "fid": "", "exists": True},
    }


def _upsert_record(
    db: Session,
    *,
    task_id: int,
    drive_type: str,
    account_name: str,
    dir_path: str,
    material_name: str,
    material_fid: str,
    status: str,
    message: str,
    config_version: int,
) -> Any:
    return task_lead_in_repo().upsert_record(
        db,
        task_id=task_id,
        drive_type=drive_type,
        account_name=account_name,
        dir_path=dir_path,
        material_name=material_name,
        material_fid=material_fid,
        status=status,
        message=message,
        config_version=config_version,
    )


def get_lead_in_record(db: Session, task_id: int, drive_type: str | None = None) -> Any:
    try:
        normalized_task_id = int(task_id or 0)
    except (TypeError, ValueError):
        return None
    if normalized_task_id <= 0:
        return None
    return task_lead_in_repo().get_record(db, normalized_task_id, drive_type)


def get_task_lead_in_state(db: Session, task: Any) -> dict[str, Any]:
    """任务详情里的引流记录摘要，无记录时 state="none"。"""
    try:
        task_id = int(getattr(task, "id", 0) or 0)
    except (TypeError, ValueError):
        task_id = 0
    if task_id <= 0:
        return {
            "state": "none",
            "message": "",
            "last_added_at": None,
            "material_name": "",
        }
    drive_type, _ = lead_materials._resolve_task_drive(db, task)
    record = get_lead_in_record(db, task_id, drive_type)
    if record is None:
        record = get_lead_in_record(db, task_id)
    if record is None:
        return {
            "state": "none",
            "message": "",
            "last_added_at": None,
            "material_name": "",
        }
    status = (
        record.status if record.status in lead_materials._ACTIVE_STATUSES else "failed"
    )
    return {
        "state": status,
        "message": str(record.message or ""),
        "last_added_at": (
            normalize_api_datetime(record.updated_at) if status == "added" else None
        ),
        "material_name": str(record.material_name or ""),
    }


def _record_sort_key(record: Any) -> tuple[datetime, int]:
    updated = record.updated_at
    if updated is None:
        updated = datetime.min
    elif updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return (updated, int(record.id or 0))


def load_lead_in_summary_map(
    db: Session, task_ids: list[int]
) -> dict[int, dict[str, Any]]:
    """一次 IN 查询批量取每个任务最近的引流记录，供任务列表填充 lead_in 字段。"""
    normalized: list[int] = []
    for task_id in task_ids:
        try:
            value = int(task_id)
        except (TypeError, ValueError):
            continue
        if value > 0 and value not in normalized:
            normalized.append(value)
    summary: dict[int, dict[str, Any]] = {
        task_id: {"state": "none", "last_added_at": None} for task_id in normalized
    }
    if not normalized:
        return summary
    records = task_lead_in_repo().list_by_task_ids(db, normalized)
    latest: dict[int, Any] = {}
    for record in records:
        task_id = int(record.task_id)
        previous = latest.get(task_id)
        if previous is None or _record_sort_key(record) > _record_sort_key(previous):
            latest[task_id] = record
    for task_id, record in latest.items():
        status = (
            record.status
            if record.status in lead_materials._ACTIVE_STATUSES
            else "failed"
        )
        summary[task_id] = {
            "state": status,
            "last_added_at": (
                normalize_api_datetime(record.updated_at) if status == "added" else None
            ),
        }
    return summary
