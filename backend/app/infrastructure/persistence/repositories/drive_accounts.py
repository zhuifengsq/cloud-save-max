"""网盘账号仓储（当前只服务少量只读查询，随 S3 逐步扩展）。"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.drive_account import DriveAccount
from app.core.runtime.cache_version import invalidate_drive_accounts


def get_drive_type_by_name(db: Session, *, name: str) -> Any:
    return db.execute(select(DriveAccount.drive_type).where(DriveAccount.name == name)).scalars().first()


def list_accounts(db: Session):
    return db.execute(select(DriveAccount).order_by(DriveAccount.is_default.desc(), DriveAccount.id.asc())).scalars().all()


def get_account(db: Session, account_id: int):
    return db.get(DriveAccount, account_id)


def find_id_by_name(db: Session, *, name: str):
    return db.execute(select(DriveAccount.id).where(DriveAccount.name == name)).first()


def list_all_accounts(db: Session):
    return db.execute(select(DriveAccount)).scalars().all()


def new_account(
    db: Session,
    *,
    name: str,
    drive_type: str,
    cookie: str,
    enabled: bool,
    is_default: bool,
    capacity_warning_threshold: int,
    config_json: str,
):
    account = DriveAccount(
        name=name,
        drive_type=drive_type,
        cookie=cookie,
        enabled=enabled,
        is_default=is_default,
        capacity_warning_threshold=capacity_warning_threshold,
    )
    account.config_json = config_json
    db.add(account)
    db.flush()
    invalidate_drive_accounts()
    return account


def delete_account(db: Session, account) -> None:
    db.delete(account)
    invalidate_drive_accounts()


def find_active_by_name(db: Session, *, name: str):
    return (
        db.execute(
            select(DriveAccount).where(
                DriveAccount.enabled.is_(True),
                DriveAccount.name == name,
                DriveAccount.runtime_status == "active",
            )
        )
        .scalars()
        .first()
    )


def find_enabled_by_name(db: Session, *, name: str):
    return db.execute(select(DriveAccount).where(DriveAccount.enabled.is_(True), DriveAccount.name == name)).scalars().first()


def find_active_by_type(db: Session, *, drive_type: str):
    return (
        db.execute(
            select(DriveAccount)
            .where(DriveAccount.enabled.is_(True), DriveAccount.drive_type == drive_type, DriveAccount.runtime_status == "active")
            .order_by(DriveAccount.is_default.desc(), DriveAccount.id.asc())
        )
        .scalars()
        .first()
    )


def find_enabled_by_type(db: Session, *, drive_type: str):
    return (
        db.execute(
            select(DriveAccount)
            .where(DriveAccount.enabled.is_(True), DriveAccount.drive_type == drive_type)
            .order_by(DriveAccount.is_default.desc(), DriveAccount.id.asc())
        )
        .scalars()
        .first()
    )


def find_any_active(db: Session):
    return (
        db.execute(
            select(DriveAccount)
            .where(DriveAccount.enabled.is_(True), DriveAccount.runtime_status == "active")
            .order_by(DriveAccount.is_default.desc(), DriveAccount.id.asc())
        )
        .scalars()
        .first()
    )


def find_any_enabled(db: Session):
    return (
        db.execute(select(DriveAccount).where(DriveAccount.enabled.is_(True)).order_by(DriveAccount.is_default.desc(), DriveAccount.id.asc()))
        .scalars()
        .first()
    )


def list_active_by_type(db: Session, *, drive_type: str):
    return (
        db.execute(
            select(DriveAccount).where(
                DriveAccount.drive_type == drive_type,
                DriveAccount.enabled == True,
                DriveAccount.runtime_status == "active",
            )
        )
        .scalars()
        .all()
    )


def list_active_drive_types(db: Session):
    return (
        db.execute(
            select(DriveAccount.drive_type).where(DriveAccount.enabled.is_(True), DriveAccount.runtime_status == "active")
        )
        .scalars()
        .all()
    )


def list_enabled_names(db: Session):
    return (
        db.execute(
            select(DriveAccount.name)
            .where(DriveAccount.enabled.is_(True))
            .order_by(DriveAccount.is_default.desc(), DriveAccount.id.asc())
        )
        .scalars()
        .all()
    )
