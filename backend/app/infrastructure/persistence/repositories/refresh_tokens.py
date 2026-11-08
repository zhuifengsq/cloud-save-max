"""刷新令牌仓储。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.persistence.models.refresh_token import RefreshToken


def add_refresh_token(
    db: Session,
    *,
    user_id: int,
    token_hash: str,
    expires_at: datetime,
    ip: str | None,
    user_agent: str | None,
    last_used_at: datetime,
) -> Any:
    row = RefreshToken(
        user_id=int(user_id),
        token_hash=token_hash,
        expires_at=expires_at,
        ip=ip,
        user_agent=user_agent,
        last_used_at=last_used_at,
    )
    db.add(row)
    return row


def get_by_token_hash(db: Session, token_hash: str) -> Any:
    return db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash)).scalars().first()
