"""Typed account service inputs, outputs, and dependency contracts.

Provider-specific configuration remains JSON-like; account identity, state and
service payload fields have concrete types and are not an Any boundary.
"""
from collections.abc import Sequence
from datetime import datetime
from typing import Any, NotRequired, Protocol, TypedDict
from sqlalchemy.orm import Session
from app.domain.ports.drive_account import DriveAccountRecord

Config = dict[str, Any]
PublicProfile = dict[str, str | int | float | bool | None]


class CreateAccountPayload(TypedDict):
    name: str
    drive_type: str
    cookie: NotRequired[str | None]
    config: NotRequired[Config | None]
    enabled: NotRequired[bool]
    is_default: NotRequired[bool]
    capacity_warning_threshold: NotRequired[int]


class UpdateAccountPayload(TypedDict, total=False):
    name: str | None
    cookie: str | None
    config: Config | None
    enabled: bool | None
    is_default: bool | None
    capacity_warning_threshold: int | None
    clear_credentials: list[str] | None


class AccountView(TypedDict):
    id: int
    name: str
    drive_type: str
    config: Config
    credential_present: bool
    credential_fields: list[str]
    profile: PublicProfile
    enabled: bool
    is_default: bool
    capacity_warning_threshold: int
    used_space: int | None
    total_space: int | None
    usage_ratio: float | None
    runtime_status: str | None
    probe_fail_count: int
    last_checked_at: datetime | None
    profile_updated_at: datetime | None
    last_error: str | None
    created_at: datetime | None
    updated_at: datetime | None


class CapacityOverview(TypedDict):
    summary: dict[str, int | float | None]
    accounts: list[AccountView]
    warning_accounts: list[AccountView]
    unsupported_accounts: list[AccountView]
    updated_at: datetime | None


class AccountIdentity(Protocol):
    @property
    def drive_type(self) -> str: ...


class AccountAdapter(Protocol):
    def init(self) -> object: ...
    def export_runtime_config(self) -> Config: ...
    def get_account_config(self) -> object: ...
    def sign_in(self) -> object: ...


class AccountAdapterFactory(Protocol):
    def init_ttl_seconds(self) -> float: ...
    def get_supported_types(self) -> list[str]: ...
    def create_adapter(self, drive_type: str, cookie: str = '', index: int = 0, *,
                       config: Config | None = None, account_name: str = '',
                       no_login: bool = False) -> AccountAdapter | None: ...


class AccountRegistry(Protocol):
    def detect_drive_type(self, url: str) -> str | None: ...
    def get_drive_type_meta(self, drive_type: str) -> Config: ...
    def supported_drive_types(self) -> list[Config]: ...
    def deserialize_cookie(self, drive_type: str, cookie: str | None) -> Config: ...
    def normalize_config(self, drive_type: str, config: Config | None) -> Config: ...
    def parse_config_json(self, drive_type: str, config_json: str | None, cookie: str | None = None) -> Config: ...
    def serialize_config(self, drive_type: str, config: Config | None) -> str: ...


class AccountRepository(Protocol):
    def list_enabled_by_type(self, db: Session, *, drive_type: str) -> Sequence[DriveAccountRecord]: ...
    def list_accounts(self, db: Session) -> Sequence[DriveAccountRecord]: ...
    def get_account(self, db: Session, account_id: int) -> DriveAccountRecord | None: ...
    def find_id_by_name(self, db: Session, *, name: str) -> object | None: ...
    def new_account(self, db: Session, *, name: str, drive_type: str, cookie: str,
                    enabled: bool, is_default: bool, capacity_warning_threshold: int,
                    config_json: str) -> DriveAccountRecord: ...
    def list_all_accounts(self, db: Session) -> Sequence[DriveAccountRecord]: ...
    def delete_account(self, db: Session, account: DriveAccountRecord) -> None: ...
    def require_read_only(self, db: Session) -> None: ...
    def release_read(self, db: Session) -> None: ...
    def is_default_constraint_error(self, error: Exception) -> bool: ...
    def find_active_by_name(self, db: Session, *, name: str) -> DriveAccountRecord | None: ...
    def find_enabled_by_name(self, db: Session, *, name: str) -> DriveAccountRecord | None: ...
    def find_active_by_type(self, db: Session, *, drive_type: str) -> DriveAccountRecord | None: ...
    def find_enabled_by_type(self, db: Session, *, drive_type: str) -> DriveAccountRecord | None: ...
    def find_any_active(self, db: Session) -> DriveAccountRecord | None: ...
    def find_any_enabled(self, db: Session) -> DriveAccountRecord | None: ...
