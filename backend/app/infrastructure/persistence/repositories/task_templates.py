"""任务模板仓储（task_templates 表；S5 从路由下沉）。"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.persistence.models.task_template import TaskTemplate


def list_rows(db: Session) -> list[Any]:
    return list(db.execute(select(TaskTemplate).order_by(TaskTemplate.id.desc())).scalars().all())


def get_row(db: Session, template_id: int) -> Any:
    return db.execute(select(TaskTemplate).where(TaskTemplate.id == int(template_id))).scalars().first()


def create_row(db: Session, *, name: str, config_json: str) -> Any:
    row = TaskTemplate(name=name, config_json=config_json)
    db.add(row)
    db.flush()
    return row


def delete_row(db: Session, row: Any) -> None:
    db.delete(row)
    db.flush()
