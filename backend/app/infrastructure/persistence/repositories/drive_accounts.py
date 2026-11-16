"""网盘账号仓储（当前只服务少量只读查询，随 S3 逐步扩展）。"""
from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import Row, select, event
from sqlalchemy.orm import ORMExecuteState, Session, SessionTransaction, UOWTransaction

from app.infrastructure.persistence.models.drive_account import DriveAccount
from app.core.runtime.cache_version import invalidate_drive_accounts
from app.core.runtime.tx import end_read_tx


_WRITE_TRANSACTION = '_account_operation_write_transaction'


@event.listens_for(Session, 'after_flush')
def _record_flush(db: Session, context: UOWTransaction) -> None:
    db.info[_WRITE_TRANSACTION] = db.get_transaction()


@event.listens_for(Session, 'do_orm_execute')
def _record_statement_write(state: ORMExecuteState) -> None:
    if not state.is_select and not str(state.statement).lstrip().upper().startswith('SELECT '):
        state.session.connection()
        state.session.info[_WRITE_TRANSACTION] = state.session.get_transaction()


@event.listens_for(Session, 'after_transaction_end')
def _forget_write_transaction(db: Session, transaction: SessionTransaction) -> None:
    if transaction.parent is None:
        db.info.pop(_WRITE_TRANSACTION, None)


def require_read_only(db: Session) -> None:
    written = db.info.get(_WRITE_TRANSACTION)
    if db.new or db.dirty or db.deleted or (written is not None and written is db.get_transaction()):
        raise RuntimeError('Account network operations require a read-only session')


def release_read(db: Session) -> None:
    require_read_only(db)
    end_read_tx(db)


def get_drive_type_by_name(db: Session, *, name: str) -> str | None:
    return db.execute(select(DriveAccount.drive_type).where(DriveAccount.name == name)).scalars().first()


def list_accounts(db: Session) -> Sequence[DriveAccount]:
    return db.execute(select(DriveAccount).order_by(DriveAccount.is_default.desc(), DriveAccount.id.asc())).scalars().all()


def get_account(db: Session, account_id: int) -> DriveAccount | None:
    return db.get(DriveAccount, account_id)


def find_id_by_name(db: Session, *, name: str) -> Row[tuple[int]] | None:
    return db.execute(select(DriveAccount.id).where(DriveAccount.name == name)).first()


def list_all_accounts(db: Session) -> Sequence[DriveAccount]:
    return db.execute(select(DriveAccount)).scalars().all()


def is_default_constraint_error(error: Exception) -> bool:
    original = getattr(error, 'orig', None)
    constraint = getattr(getattr(original, 'diag', None), 'constraint_name', None)
    return (constraint == 'uq_drive_accounts_single_default'
            or 'drive_accounts.is_default' in str(original))


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
) -> DriveAccount:
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


def delete_account(db: Session, account: object) -> None:
    db.delete(account)
    invalidate_drive_accounts()


def find_active_by_name(db: Session, *, name: str) -> DriveAccount | None:
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


def find_enabled_by_name(db: Session, *, name: str) -> DriveAccount | None:
    return db.execute(select(DriveAccount).where(DriveAccount.enabled.is_(True), DriveAccount.name == name)).scalars().first()


def find_active_by_type(db: Session, *, drive_type: str) -> DriveAccount | None:
    return (
        db.execute(
            select(DriveAccount)
            .where(DriveAccount.enabled.is_(True), DriveAccount.drive_type == drive_type, DriveAccount.runtime_status == "active")
            .order_by(DriveAccount.is_default.desc(), DriveAccount.id.asc())
        )
        .scalars()
        .first()
    )


def find_enabled_by_type(db: Session, *, drive_type: str) -> DriveAccount | None:
    return (
        db.execute(
            select(DriveAccount)
            .where(DriveAccount.enabled.is_(True), DriveAccount.drive_type == drive_type)
            .order_by(DriveAccount.is_default.desc(), DriveAccount.id.asc())
        )
        .scalars()
        .first()
    )


def find_any_active(db: Session) -> DriveAccount | None:
    return (
        db.execute(
            select(DriveAccount)
            .where(DriveAccount.enabled.is_(True), DriveAccount.runtime_status == "active")
            .order_by(DriveAccount.is_default.desc(), DriveAccount.id.asc())
        )
        .scalars()
        .first()
    )


def find_any_enabled(db: Session) -> DriveAccount | None:
    return (
        db.execute(select(DriveAccount).where(DriveAccount.enabled.is_(True)).order_by(DriveAccount.is_default.desc(), DriveAccount.id.asc()))
        .scalars()
        .first()
    )


def list_active_by_type(db: Session, *, drive_type: str) -> Sequence[DriveAccount]:
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


def list_active_drive_types(db: Session) -> Sequence[str]:
    return (
        db.execute(
            select(DriveAccount.drive_type).where(DriveAccount.enabled.is_(True), DriveAccount.runtime_status == "active")
        )
        .scalars()
        .all()
    )


def list_enabled_names(db: Session) -> Sequence[str]:
    return (
        db.execute(
            select(DriveAccount.name)
            .where(DriveAccount.enabled.is_(True))
            .order_by(DriveAccount.is_default.desc(), DriveAccount.id.asc())
        )
        .scalars()
        .all()
    )

def list_enabled_by_type(db: Session, *, drive_type: str) -> list[DriveAccount]:
    """指定网盘类型的全部启用账号（id 升序；调用方自行做默认/在线优先级排序）。"""
    return list(
        db.execute(
            select(DriveAccount)
            .where(DriveAccount.enabled.is_(True), DriveAccount.drive_type == drive_type)
            .order_by(DriveAccount.id.asc())
        )
        .scalars()
        .all()
    )
