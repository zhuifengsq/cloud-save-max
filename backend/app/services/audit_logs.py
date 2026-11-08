from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.application.container import audit_repo


def list_audit_logs(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 20,
    q: str | None = None,
    action: str | None = None,
    success: bool | None = None,
) -> "tuple[list[Any], int]":
    return audit_repo().list_audit_logs(
        db, page=page, page_size=page_size, q=q, action=action, success=success
    )
