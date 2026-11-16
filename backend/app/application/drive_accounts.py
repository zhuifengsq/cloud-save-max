from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from collections.abc import Mapping
from typing import Any, Unpack
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.core.errors import ApiError, bad_request, not_found
from app.core.drive_auth import DriveAuthRequired
from app.domain.ports.drive_account import DriveAccountRecord
from app.core.account_privacy import SAFE_ACCOUNT_ERROR, public_account_profile
from app.core.credential_crypto import is_secret_key, public_config_keys
from app.core.runtime.cache_version import invalidate_drive_accounts
from app.application.container import adapter_factory as _acc_AdapterFactory; AdapterFactory = _acc_AdapterFactory()
from app.application.container import adapter_registry as _acc_AdapterRegistry; AdapterRegistry = _acc_AdapterRegistry()
from app.application.container import drive_accounts_repo
from app.application.drive_account_auth_sessions import create_auth_session
from app.application.drive_account_operation import AccountOperation, ProbeOutcome
from app.application.drive_account_types import (
    AccountAdapter, AccountIdentity, AccountView, CapacityOverview, Config,
    CreateAccountPayload, PublicProfile, UpdateAccountPayload,
)

BEIJING_TZ = ZoneInfo('Asia/Shanghai')
logger = logging.getLogger(__name__)


def list_drive_accounts(db: Session) -> list[DriveAccountRecord]:
    return list(drive_accounts_repo().list_accounts(db))


def get_drive_account(db: Session, account_id: int) -> DriveAccountRecord:
    account = drive_accounts_repo().get_account(db, account_id)
    if account is None:
        raise not_found('DRIVE_ACCOUNT_NOT_FOUND', '驱动账号不存在')
    return account


def create_drive_account(db: Session, **payload: Unpack[CreateAccountPayload]) -> DriveAccountRecord:
    # 账号快照是进程内缓存：任何写操作都要让缓存失效（写路径见 cache_version.py）。
    invalidate_drive_accounts()
    if payload['drive_type'] not in AdapterFactory.get_supported_types():
        raise bad_request('DRIVE_TYPE_INVALID', '不支持的驱动类型')
    exists = drive_accounts_repo().find_id_by_name(db, name=payload['name'])
    if exists:
        raise bad_request('DRIVE_ACCOUNT_EXISTS', '驱动账号名称已存在')
    config, cookie = _normalize_account_payload(
        payload['drive_type'],
        payload.get('config'),
        payload.get('cookie'),
    )
    account = drive_accounts_repo().new_account(
        db,
        name=payload['name'],
        drive_type=payload['drive_type'],
        cookie=cookie,
        enabled=payload.get('enabled', True),
        is_default=False,
        capacity_warning_threshold=payload.get('capacity_warning_threshold', 85),
        config_json=json.dumps(config, ensure_ascii=False),
    )
    if payload.get('is_default'):
        set_default_drive_account(db, account.id)
    return account


def update_drive_account(db: Session, account_id: int, **payload: Unpack[UpdateAccountPayload]) -> DriveAccountRecord:
    invalidate_drive_accounts()
    account = get_drive_account(db, account_id)
    clear_credentials = payload.pop('clear_credentials', []) or []
    for key, value in payload.items():
        if key in {'cookie', 'config', 'is_default'}:
            continue
        if value is not None:
            setattr(account, key, value)
    if payload.get('is_default') is False:
        account.is_default = False
    if 'config' in payload or 'cookie' in payload or clear_credentials:
        config, cookie = _normalize_account_payload(
            account.drive_type,
            payload.get('config'),
            payload.get('cookie'),
            current_config_json=account.config_json,
            current_cookie=account.cookie,
            clear_credentials=clear_credentials,
        )
        account.set_credentials(cookie=cookie, config_json=json.dumps(config, ensure_ascii=False))
    try:
        db.flush()
    except StaleDataError:
        raise ApiError(code='DRIVE_ACCOUNT_CONFLICT', message='账号已被其他操作修改，请刷新后重试', http_status=409) from None
    if payload.get('is_default'):
        set_default_drive_account(db, account.id)
    return account


def set_drive_account_enabled(db: Session, account_id: int, enabled: bool) -> DriveAccountRecord:
    invalidate_drive_accounts()
    account = get_drive_account(db, account_id)
    account.enabled = enabled
    return account


def set_default_drive_account(db: Session, account_id: int) -> DriveAccountRecord:
    invalidate_drive_accounts()
    account = get_drive_account(db, account_id)
    items = drive_accounts_repo().list_all_accounts(db)
    try:
        for item in items:
            if item.id != account.id:
                item.is_default = False
        # Clear before setting, independently of SQLAlchemy's primary-key flush order.
        db.flush()
        account.is_default = True
        db.flush()
    except StaleDataError:
        raise ApiError(code='DRIVE_ACCOUNT_CONFLICT', message='账号已被其他操作修改，请刷新后重试', http_status=409) from None
    except IntegrityError as exc:
        if not drive_accounts_repo().is_default_constraint_error(exc):
            raise
        raise ApiError(code='DRIVE_ACCOUNT_DEFAULT_CONFLICT', message='默认账号已被其他操作修改，请刷新后重试', http_status=409) from None
    return account


def delete_drive_account(db: Session, account_id: int) -> None:
    invalidate_drive_accounts()
    account = get_drive_account(db, account_id)
    drive_accounts_repo().delete_account(db, account)


def release_drive_account_read(db: Session) -> None:
    drive_accounts_repo().release_read(db)


def _account_operation(db: Session, account_id: int, config: Config | None = None,
                       expected_version: int | None = None) -> AccountOperation:
    drive_accounts_repo().require_read_only(db)
    account = get_drive_account(db, account_id)
    if expected_version is not None and account.row_version != expected_version:
        raise StaleDataError('Account authentication snapshot is stale')
    if config is None:
        runtime_config = AdapterRegistry.parse_config_json(account.drive_type, account.config_json, account.cookie)
        runtime_cookie = AdapterRegistry.serialize_config(account.drive_type, runtime_config)
    else:
        runtime_config, runtime_cookie = _normalize_account_payload(account.drive_type, config, None,
            current_config_json=account.config_json, current_cookie=account.cookie)
    return AccountOperation(account.id, account.row_version, account.name, account.drive_type,
                            runtime_config, runtime_cookie)


def prepare_drive_account_auth(db: Session, account_id: int, expected_version: int | None = None) -> int:
    drive_accounts_repo().require_read_only(db)
    account = get_drive_account(db, account_id)
    row_version = account.row_version
    if expected_version is not None and row_version != expected_version:
        raise StaleDataError('Account authentication snapshot is stale')
    release_drive_account_read(db)
    return int(row_version)


def _account_for_write(db: Session, plan: AccountOperation) -> DriveAccountRecord:
    account = drive_accounts_repo().get_account(db, plan.id)
    if account is None or account.row_version != plan.row_version:
        raise StaleDataError('Account operation snapshot is stale')
    return account


def _operation_adapter(plan: AccountOperation) -> AccountAdapter | None:
    return AdapterFactory.create_adapter(plan.drive_type, plan.cookie, config=plan.config, account_name=plan.name)


def _auth_required(plan: AccountOperation, adapter: AccountAdapter, exc: DriveAuthRequired) -> ApiError:
    session = create_auth_session(account_id=plan.id, drive_type=plan.drive_type,
        method=exc.method, adapter=exc.adapter or adapter, payload=exc.payload, row_version=plan.row_version)
    return ApiError(code='DRIVE_ACCOUNT_AUTH_REQUIRED', message=exc.message or '需要二次认证', http_status=409,
        detail=json.dumps({'account_id': plan.id, 'drive_type': plan.drive_type, 'method': exc.method,
                           'session_id': session.session_id, 'payload': exc.payload}, ensure_ascii=False))


def _probe_operation(plan: AccountOperation, *, auth_completed: bool = False) -> ProbeOutcome:
    outcome = ProbeOutcome('error', datetime.now(timezone.utc), SAFE_ACCOUNT_ERROR)
    adapter = _operation_adapter(plan)
    if adapter is None:
        return outcome
    try:
        if not adapter.init():
            outcome.status = 'inactive'
            return outcome
    except DriveAuthRequired as exc:
        # Guangya can demand SMS again while fetching profile after successful SMS.
        if auth_completed and plan.drive_type == 'guangya':
            return ProbeOutcome('active', outcome.checked_at, config=plan.config,
                profile={'drive_type': plan.drive_type, 'drive_name': plan.drive_type,
                    'nickname': str(getattr(adapter, 'nickname', '') or plan.config.get('phone_number') or plan.name),
                    'username': str(plan.config.get('phone_number') or plan.name)}, profile_updated_at=datetime.now(timezone.utc))
        raise _auth_required(plan, adapter, exc) from None
    except Exception as exc:
        logger.warning('Drive initialization failed account_id=%s error_type=%s', plan.id, type(exc).__name__)
        return outcome
    outcome.status, outcome.error = 'active', None
    try:
        outcome.config = adapter.export_runtime_config()
        outcome.profile = _build_account_profile(adapter, plan)
        if outcome.profile is not None:
            outcome.profile_updated_at = datetime.now(timezone.utc)
    except DriveAuthRequired as exc:
        if not (auth_completed and plan.drive_type == 'guangya'):
            raise _auth_required(plan, adapter, exc) from None
    except Exception as exc:
        # Profile/config refresh failure does not invalidate successful authentication.
        outcome.error = SAFE_ACCOUNT_ERROR
        logger.warning('Drive profile refresh failed account_id=%s error_type=%s', plan.id, type(exc).__name__)
    return outcome


def _apply_probe(account: DriveAccountRecord, outcome: ProbeOutcome) -> DriveAccountRecord:
    invalidate_drive_accounts()
    account.last_checked_at = outcome.checked_at
    account.runtime_status, account.last_error = outcome.status, outcome.error
    if outcome.config is not None:
        account.set_credentials(cookie=AdapterRegistry.serialize_config(account.drive_type, outcome.config),
                                config_json=json.dumps(outcome.config, ensure_ascii=False))
    if outcome.profile is not None:
        account.profile_json = json.dumps(outcome.profile, ensure_ascii=False)
        account.profile_updated_at = outcome.profile_updated_at
    account.probe_fail_count = 0 if outcome.status == 'active' else int(account.probe_fail_count or 0) + 1
    if account.probe_fail_count >= 3:
        account.enabled = False
    return account


def probe_drive_account(db: Session, account_id: int, *, config: Config | None = None,
                        auth_completed: bool = False, expected_version: int | None = None) -> DriveAccountRecord:
    plan = _account_operation(db, account_id, config, expected_version)
    release_drive_account_read(db)
    outcome = _probe_operation(plan, auth_completed=auth_completed)
    return _apply_probe(_account_for_write(db, plan), outcome)


def sign_in_drive_account(db: Session, account_id: int) -> dict[str, Any]:
    plan = _account_operation(db, account_id)
    release_drive_account_read(db)
    adapter = _operation_adapter(plan)
    if adapter is None:
        raise bad_request("DRIVE_SIGNIN_FAILED", "驱动实例创建失败")
    try:
        result = adapter.sign_in()
    except Exception:
        raise ApiError(code='DRIVE_SIGNIN_FAILED', message=SAFE_ACCOUNT_ERROR, http_status=502) from None
    if not isinstance(result, dict) or not result.get("supported"):
        raise bad_request("DRIVE_SIGNIN_UNSUPPORTED", "该网盘暂不支持签到")
    if not result.get("ok", True):
        raise bad_request('DRIVE_SIGNIN_FAILED', SAFE_ACCOUNT_ERROR)
    config_snapshot = adapter.export_runtime_config()
    account = _account_for_write(db, plan)
    if isinstance(config_snapshot, dict) and config_snapshot:
        invalidate_drive_accounts()
        account.set_credentials(cookie=AdapterRegistry.serialize_config(account.drive_type, config_snapshot),
                                config_json=json.dumps(config_snapshot, ensure_ascii=False))
        db.flush()
    return result


def supported_drive_types() -> list[dict[str, Any]]:
    types = [dict(item, config_fields=[dict(field) for field in item.get('config_fields', [])])
             for item in AdapterRegistry.supported_drive_types()]
    for item in types:
        for field in item.get('config_fields', []):
            if field['key'] not in _public_config_keys(item['code']):
                field['secret'] = True
    return types


def resolve_drive_account_config(account: DriveAccountRecord) -> Config:
    return AdapterRegistry.parse_config_json(account.drive_type, account.config_json, account.cookie)


def resolve_drive_account_profile(account: DriveAccountRecord) -> dict[str, Any]:
    if not account.profile_json:
        return {}
    try:
        profile = json.loads(account.profile_json)
    except (TypeError, ValueError):
        return {}
    return profile if isinstance(profile, dict) else {}


def extract_capacity_metrics(profile: Mapping[str, str | int | float | bool | None]) -> tuple[int | None, int | None, float | None]:
    used_value = profile.get('used_space')
    total_value = profile.get('total_space')
    try:
        used_space = int(used_value) if used_value is not None else None
    except (TypeError, ValueError):
        used_space = None
    try:
        total_space = int(total_value) if total_value is not None else None
    except (TypeError, ValueError):
        total_space = None

    usage_ratio: float | None = None
    if used_space is not None and total_space and total_space > 0:
        usage_ratio = round(used_space / total_space, 4)
    return used_space, total_space, usage_ratio


def serialize_drive_account(account: DriveAccountRecord) -> AccountView:
    raw_profile = resolve_drive_account_profile(account)
    profile = public_account_profile(raw_profile)
    config = resolve_drive_account_config(account)
    public_keys = _public_config_keys(account.drive_type)
    credential_fields = sorted(key for key, value in config.items() if key not in public_keys and value not in (None, '', False))
    used_space, total_space, usage_ratio = extract_capacity_metrics(profile)
    return {
        'id': account.id,
        'name': account.name,
        'drive_type': account.drive_type,
        'config': {key: value for key, value in config.items() if key in public_keys},
        'credential_present': bool(credential_fields),
        'credential_fields': credential_fields,
        'profile': profile,
        'enabled': account.enabled,
        'is_default': account.is_default,
        'capacity_warning_threshold': account.capacity_warning_threshold,
        'used_space': used_space,
        'total_space': total_space,
        'usage_ratio': usage_ratio,
        'runtime_status': account.runtime_status,
        'probe_fail_count': int(getattr(account, "probe_fail_count", 0) or 0),
        'last_checked_at': normalize_api_datetime(account.last_checked_at),
        'profile_updated_at': normalize_api_datetime(account.profile_updated_at),
        'last_error': '账号操作失败，请检查登录状态或重新认证' if account.last_error else None,
        'created_at': normalize_api_datetime(account.created_at),
        'updated_at': normalize_api_datetime(account.updated_at),
    }


def refresh_drive_account_profiles(db: Session) -> list[DriveAccountRecord]:
    drive_accounts_repo().require_read_only(db)
    plans = [_account_operation(db, account.id) for account in list_drive_accounts(db)]
    release_drive_account_read(db)
    outcomes = [_probe_operation(plan) for plan in plans]
    with db.no_autoflush:
        accounts = [_account_for_write(db, plan) for plan in plans]
        return [_apply_probe(account, outcome) for account, outcome in zip(accounts, outcomes)]


def build_capacity_overview(db: Session) -> CapacityOverview:
    accounts = [serialize_drive_account(item) for item in list_drive_accounts(db)]
    total_used_space = 0
    total_capacity = 0
    capacity_account_count = 0
    warning_accounts: list[AccountView] = []
    unsupported_accounts: list[AccountView] = []

    for item in accounts:
        usage_ratio = item['usage_ratio']
        total_space = item['total_space']
        used_space = item['used_space']
        threshold = item['capacity_warning_threshold']
        if used_space is not None and total_space is not None and total_space > 0:
            total_used_space += used_space
            total_capacity += total_space
            capacity_account_count += 1
            if usage_ratio is not None and usage_ratio >= threshold / 100:
                warning_accounts.append(item)
        else:
            unsupported_accounts.append(item)

    warning_accounts.sort(key=lambda item: (item['usage_ratio'] is None, -(item['usage_ratio'] or 0), item['name']))
    usage_ratio = round(total_used_space / total_capacity, 4) if total_capacity > 0 else None

    return {
        'summary': {
            'account_count': len(accounts),
            'enabled_account_count': sum(1 for item in accounts if item['enabled']),
            'capacity_account_count': capacity_account_count,
            'warning_account_count': len(warning_accounts),
            'total_used_space': total_used_space or None,
            'total_space': total_capacity or None,
            'usage_ratio': usage_ratio,
        },
        'accounts': accounts,
        'warning_accounts': warning_accounts,
        'unsupported_accounts': unsupported_accounts,
        'updated_at': max((item['profile_updated_at'] for item in accounts if item['profile_updated_at']), default=None),
    }


def _build_account_profile(adapter: AccountAdapter, account: AccountIdentity) -> PublicProfile | None:
    profile = adapter.get_account_config()
    if not isinstance(profile, dict):
        return None
    profile.setdefault('drive_type', account.drive_type)
    profile.setdefault('drive_name', account.drive_type)
    profile.setdefault('nickname', '')
    profile.setdefault('username', '')
    profile.setdefault('used_space', None)
    profile.setdefault('total_space', None)
    return public_account_profile(profile)


def normalize_api_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(BEIJING_TZ)


def _public_config_keys(drive_type: str) -> set[str]:
    fields = AdapterRegistry.get_drive_type_meta(drive_type).get('config_fields', [])
    return {str(field['key']) for field in fields
            if not field.get('secret') and field.get('input_type') != 'password'
            and not is_secret_key(str(field['key'])) and field['key'] in public_config_keys(drive_type)}


def _normalize_account_payload(
    drive_type: str,
    config: Config | None,
    cookie: str | None,
    *,
    current_config_json: str | None = None,
    current_cookie: str | None = None,
    clear_credentials: list[str] | None = None,
) -> tuple[Config, str]:
    current = (AdapterRegistry.parse_config_json(drive_type, current_config_json, current_cookie)
               if current_config_json is not None or current_cookie is not None else {})
    public_keys = _public_config_keys(drive_type)
    clear = set(clear_credentials or [])
    if any(key in public_keys or key not in current for key in clear):
        raise bad_request('DRIVE_CREDENTIAL_CLEAR_INVALID', '只能清除已保存的登录凭据字段')
    if config is None and cookie is not None:
        if cookie.strip() in {'********', '••••••••', '******'}:
            raise bad_request('DRIVE_CREDENTIAL_MASK_INVALID', '请填写新的登录凭据，或留空保持不变')
        meta = AdapterRegistry.get_drive_type_meta(drive_type)
        decoded = AdapterRegistry.deserialize_cookie(drive_type, cookie)
        if meta.get('config_format') == 'kv':
            provided = {part.split('=', 1)[0].strip() for part in cookie.split(';') if '=' in part}
        else:
            fields = meta.get('config_fields') or []
            provided = {str(fields[0]['key'])} if fields else {'cookie'}
        config = {key: value for key, value in decoded.items() if key in provided}
    if config is not None:
        incoming = dict(config)
        for key, value in list(incoming.items()):
            if key not in public_keys:
                if isinstance(value, str) and value.strip() in {'********', '••••••••', '******'}:
                    raise bad_request('DRIVE_CREDENTIAL_MASK_INVALID', '请填写新的登录凭据，或留空保持不变')
                if current and value in (None, ''):
                    incoming.pop(key)
        normalized_config = AdapterRegistry.normalize_config(drive_type, {**current, **incoming})
    elif current_config_json is not None or current_cookie is not None:
        normalized_config = AdapterRegistry.parse_config_json(drive_type, current_config_json, current_cookie)
    else:
        raise bad_request('DRIVE_ACCOUNT_CONFIG_REQUIRED', '请填写驱动账号登录参数')
    for key in clear:
        normalized_config[key] = ''
    runtime_cookie = AdapterRegistry.serialize_config(drive_type, normalized_config)
    if not runtime_cookie.strip() and not clear:
        raise bad_request('DRIVE_ACCOUNT_CONFIG_REQUIRED', '请填写驱动账号登录参数')
    return normalized_config, runtime_cookie



def get_active_account(db: Session, account_name: str) -> DriveAccountRecord | None:
    """按名称取可用账号：优先 runtime_status=active，取不到再退回 enabled 账号。"""
    name = str(account_name or "").strip()
    if not name:
        return None
    active = drive_accounts_repo().find_active_by_name(db, name=name)
    if active is not None:
        return active
    return drive_accounts_repo().find_enabled_by_name(db, name=name)


def pick_default_account_name(db: Session, drive_type: str) -> str | None:
    """按网盘类型挑默认账号名（默认账号优先、再按 id 升序），没有可用账号返回 None。"""
    active = drive_accounts_repo().find_active_by_type(db, drive_type=drive_type)
    if active is not None:
        return active.name
    fallback = drive_accounts_repo().find_enabled_by_type(db, drive_type=drive_type)
    return None if fallback is None else fallback.name


def pick_any_default_account(db: Session) -> DriveAccountRecord | None:
    """不限网盘类型地挑一个默认账号（默认账号优先、再按 id 升序）。"""
    active = drive_accounts_repo().find_any_active(db)
    if active is not None:
        return active
    return drive_accounts_repo().find_any_enabled(db)
