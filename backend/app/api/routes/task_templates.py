from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.application.container import get_db, task_templates_repo, transaction

router = APIRouter()


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    config: dict[str, Any] = Field(default_factory=dict)


class TemplateUpdateIn(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    config: dict[str, Any] | None = None


class TemplateOut(BaseModel):
    id: int
    name: str
    config: dict[str, Any]
    created_at: str | None = None
    updated_at: str | None = None


def _to_out(row) -> TemplateOut:
    config = {}
    try:
        config = json.loads(row.config_json) if row.config_json else {}
    except Exception:
        pass
    return TemplateOut(
        id=row.id,
        name=row.name,
        config=config,
        created_at=str(row.created_at) if row.created_at else None,
        updated_at=str(row.updated_at) if row.updated_at else None,
    )


@router.get("", response_model=list[TemplateOut])
def list_templates(db: Session = Depends(get_db)):
    return [_to_out(r) for r in task_templates_repo().list_rows(db)]


@router.post("", response_model=TemplateOut)
def create_template(payload: TemplateIn, db: Session = Depends(get_db)):
    with transaction(db):
        row = task_templates_repo().create_row(
            db,
            name=payload.name,
            config_json=json.dumps(payload.config, ensure_ascii=False),
        )
    db.refresh(row)
    return _to_out(row)


@router.patch("/{template_id:int}", response_model=TemplateOut)
def update_template(template_id: int, payload: TemplateUpdateIn, db: Session = Depends(get_db)):
    row = task_templates_repo().get_row(db, template_id)
    if row is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    with transaction(db):
        if payload.name is not None:
            row.name = payload.name
        if payload.config is not None:
            row.config_json = json.dumps(payload.config, ensure_ascii=False)
    db.refresh(row)
    return _to_out(row)


@router.delete("/{template_id:int}")
def delete_template(template_id: int, db: Session = Depends(get_db)):
    row = task_templates_repo().get_row(db, template_id)
    if row is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    with transaction(db):
        task_templates_repo().delete_row(db, row)
    return {"success": True}
