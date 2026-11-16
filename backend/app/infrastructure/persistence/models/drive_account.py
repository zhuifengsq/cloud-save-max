from __future__ import annotations

from datetime import datetime
import json
from uuid import uuid4
from collections.abc import Iterable
from typing import Any

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, func, event
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapped, Mapper, InstanceState, ORMExecuteState, mapped_column, attributes, Session
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.ext.hybrid import hybrid_property
from app.core.credential_crypto import CredentialError, load_keyring, public_config_keys, seal, unseal
from app.core.account_privacy import SAFE_ACCOUNT_ERROR, sanitize_profile_json

from app.infrastructure.persistence.base import Base


class DriveAccount(Base):
    __tablename__ = "drive_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    drive_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    _stored_cookie: Mapped[str] = mapped_column('cookie', Text, nullable=False, default='')
    _stored_config_json: Mapped[str | None] = mapped_column('config_json', Text, nullable=True)
    credential_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    credential_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default='0')
    credential_blob: Mapped[str | None] = mapped_column(Text, nullable=True)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default='1')
    profile_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="1")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="0")
    capacity_warning_threshold: Mapped[int] = mapped_column(Integer, nullable=False, server_default="85")
    runtime_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    profile_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    probe_fail_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __mapper_args__ = {'version_id_col': row_version}
    __table_args__ = (
        Index('uq_drive_accounts_single_default', is_default, unique=True,
              sqlite_where=is_default.is_(True), postgresql_where=is_default.is_(True)),
    )

    def set_credentials(self, *, cookie: str, config_json: str | None) -> None:
        """Typed write boundary; retain hybrid setters for existing ORM callers."""
        self.cookie = cookie
        self.config_json = config_json

    def _secrets(self) -> dict[str, Any]:
        if self.credential_version not in (None, 0, 1):
            raise CredentialError()
        if self.credential_version == 1:
            if not self.credential_blob or not self.credential_uuid:
                raise CredentialError()
            return unseal(self.credential_blob, self.credential_uuid, self.drive_type)
        if self.credential_blob:
            raise CredentialError()
        return {'cookie': self._stored_cookie or '', 'config': {}}

    @hybrid_property
    def cookie(self) -> str:
        if '_pending_cookie' in self.__dict__:
            pending: str = self.__dict__['_pending_cookie']
            return pending
        cookie: str = self._secrets()['cookie']
        return cookie

    @cookie.inplace.setter
    def _set_cookie(self, value: str) -> None:
        self.__dict__['_pending_cookie'] = value
        attributes.flag_dirty(self)

    @cookie.inplace.expression
    @classmethod
    def _cookie_expression(cls) -> InstrumentedAttribute[str]:
        return cls._stored_cookie

    @hybrid_property
    def config_json(self) -> str | None:
        if '_pending_config' in self.__dict__:
            pending: str | None = self.__dict__['_pending_config']
            return pending
        if self.credential_version == 1:
            secrets = self._secrets()
            if not secrets.get('config_present', True):
                return None
            return json.dumps({**json.loads(self._stored_config_json or '{}'), **secrets['config']})
        self._secrets()  # validate version/blob before any legacy fallback
        return self._stored_config_json

    @config_json.inplace.setter
    def _set_config_json(self, value: str | None) -> None:
        self.__dict__['_pending_config'] = value
        attributes.flag_dirty(self)

    @config_json.inplace.expression
    @classmethod
    def _config_json_expression(cls) -> InstrumentedAttribute[str | None]:
        return cls._stored_config_json


@event.listens_for(DriveAccount, 'before_insert')
@event.listens_for(DriveAccount, 'before_update')
def _protect_credentials(mapper: Mapper[DriveAccount], connection: Connection, account: DriveAccount) -> None:
    # Profiles and exception text must never become a plaintext credential side channel.
    account.profile_json = sanitize_profile_json(account.profile_json)
    account.last_error = SAFE_ACCOUNT_ERROR if account.last_error else None
    if not any(key in account.__dict__ for key in ('_pending_cookie', '_pending_config')):
        return
    cookie, raw_config = account.cookie, account.config_json
    ring = load_keyring(required=account.credential_version == 1)
    if ring is None:
        account._stored_cookie, account._stored_config_json = cookie, raw_config
    else:
        config = json.loads(raw_config or '{}')
        public = public_config_keys(account.drive_type)
        visible = {key: value for key, value in config.items() if key in public}
        secret = {key: value for key, value in config.items() if key not in public}
        account.credential_uuid = account.credential_uuid or str(uuid4())
        account.credential_blob = seal({'cookie': cookie, 'config': secret, 'config_present': bool(raw_config)}, account.credential_uuid, account.drive_type)
        account.credential_version = 1
        account._stored_cookie = ''
        account._stored_config_json = json.dumps(visible)
    account.__dict__.pop('_pending_cookie', None)
    account.__dict__.pop('_pending_config', None)


@event.listens_for(DriveAccount, 'expire', raw=True)
def _discard_pending_credentials(state: InstanceState[DriveAccount], names: Iterable[str] | None) -> None:
    # During rollback the instance itself may already have been garbage collected.
    state.dict.pop('_pending_cookie', None)
    state.dict.pop('_pending_config', None)


@event.listens_for(Session, 'do_orm_execute')
def _reject_account_bulk_writes(execute_state: ORMExecuteState) -> None:
    # Bulk ORM writes bypass mapper privacy hooks and version checks, even for v0.
    if ((execute_state.is_update or execute_state.is_delete) and execute_state.bind_mapper is not None
            and execute_state.bind_mapper.class_ is DriveAccount):
        raise CredentialError()
