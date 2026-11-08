"""魔术正则规则仓储：只在这里碰 MagicRegexRule 模型与 SQL。"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.magic_regex_rule import MagicRegexRule


def get_rule(db: Session, key: str) -> Any:
    return db.execute(select(MagicRegexRule).where(MagicRegexRule.key == key)).scalars().first()


def list_rules(db: Session) -> list[Any]:
    return db.execute(select(MagicRegexRule).order_by(MagicRegexRule.key.asc())).scalars().all()


def create_rule(
    db: Session,
    *,
    key: str,
    label: str | None,
    pattern: str,
    replace: str,
    enabled: bool,
) -> Any:
    row = MagicRegexRule(key=key, label=label, pattern=pattern, replace=replace, enabled=enabled)
    db.add(row)
    db.flush()
    return row


def delete_rule(db: Session, row: Any) -> None:
    db.delete(row)
    db.flush()
