from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.application.container import permissions_repo


def list_permissions(db: Session) -> list[Any]:
    return permissions_repo().list_permissions(db)
