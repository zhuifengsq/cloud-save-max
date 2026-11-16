from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

import json

from app.core.errors import ApiError, bad_request, not_found
from app.api.deps import CurrentUser, get_current_user, require_permissions
from app.core.permissions import DRIVE_ACCOUNT_READ, DRIVE_ACCOUNT_WRITE
from app.application.container import get_db, transaction
from app.schemas.drive_account import DriveAccountCreateIn, DriveAccountOut, DriveAccountStatusIn, DriveAccountUpdateIn, DriveTypeOut
from app.schemas.drive_account_auth import DriveAccountCaptchaSubmitIn, DriveAccountSmsSubmitIn
from app.schemas.drive_account_probe_scheduler import DriveAccountProbeSchedulerSettingOut, DriveAccountProbeSchedulerSettingUpdateIn
from app.application.container import adapter_registry as _acc_AdapterRegistry; AdapterRegistry = _acc_AdapterRegistry()
from app.application.container import task_scheduler as _acc_task_scheduler_manager; task_scheduler_manager = _acc_task_scheduler_manager()
from app.application.drive_account_probe_scheduler import get_or_create_drive_account_probe_scheduler_setting, load_drive_account_probe_scheduler_setting, update_drive_account_probe_scheduler_setting
from app.application.container import drive_account_auth_use_case
from app.application import audit
from app.application.drive_accounts import (
    create_drive_account,
    delete_drive_account,
    list_drive_accounts,
    probe_drive_account,
    refresh_drive_account_profiles,
    serialize_drive_account,
    sign_in_drive_account,
    set_default_drive_account,
    set_drive_account_enabled,
    supported_drive_types,
    update_drive_account,
)

router = APIRouter()


def _out(item) -> DriveAccountOut:
    return DriveAccountOut(**serialize_drive_account(item))


@router.get('/auth/{session_id}', dependencies=[Depends(require_permissions(DRIVE_ACCOUNT_READ))])
def get_auth_session_status(session_id: str):
    session = drive_account_auth_use_case().status(session_id)
    if session is None:
        raise not_found("DRIVE_ACCOUNT_AUTH_SESSION_NOT_FOUND", "认证会话已失效")
    return {"account_id": session.account_id, "drive_type": session.drive_type, "method": session.method, "session_id": session.session_id, "payload": session.payload}


@router.get('', response_model=list[DriveAccountOut], dependencies=[Depends(require_permissions(DRIVE_ACCOUNT_READ))])
def get_accounts(db: Session = Depends(get_db)):
    return [_out(item) for item in list_drive_accounts(db)]


def _probe_scheduler_out(item) -> DriveAccountProbeSchedulerSettingOut:
    return DriveAccountProbeSchedulerSettingOut(
        enabled=bool(getattr(item, "enabled", True)),
        crontab=str(getattr(item, "crontab", "0 4 * * *") or "0 4 * * *"),
        timezone=str(getattr(item, "timezone", "Asia/Shanghai") or "Asia/Shanghai"),
        enabled_only=bool(getattr(item, "enabled_only", True)),
    )


@router.get('/types', response_model=list[DriveTypeOut], dependencies=[Depends(require_permissions(DRIVE_ACCOUNT_READ))])
def get_drive_types():
    return [DriveTypeOut(**item) for item in supported_drive_types()]


@router.get('/probe/scheduler', response_model=DriveAccountProbeSchedulerSettingOut, dependencies=[Depends(require_permissions(DRIVE_ACCOUNT_READ))])
def get_drive_account_probe_scheduler_setting(db: Session = Depends(get_db)):
    setting = load_drive_account_probe_scheduler_setting(db)
    if setting is not None:
        # 纯读请求不提交、不 refresh：只回滚只读事务，省掉一次全行查询
        return _probe_scheduler_out(setting)
    with transaction(db):
        setting = get_or_create_drive_account_probe_scheduler_setting(db)
        payload = _probe_scheduler_out(setting)
    return payload


@router.patch('/probe/scheduler', response_model=DriveAccountProbeSchedulerSettingOut, dependencies=[Depends(require_permissions(DRIVE_ACCOUNT_WRITE))])
def patch_drive_account_probe_scheduler_setting(request: Request, payload: DriveAccountProbeSchedulerSettingUpdateIn, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    setting = update_drive_account_probe_scheduler_setting(db, **payload.model_dump(exclude_unset=True))
    with transaction(db):
        audit.write_audit_log(db, actor_user_id=current.user.id, action='drive_account.probe_scheduler.update', target_type='drive_account_probe_scheduler_setting', target_id=str(setting.id), ip=request.client.host if request.client else None, user_agent=request.headers.get('user-agent'), success=True)
    db.refresh(setting)
    task_scheduler_manager.reload()
    return _probe_scheduler_out(setting)


@router.post('', response_model=DriveAccountOut, dependencies=[Depends(require_permissions(DRIVE_ACCOUNT_WRITE))])
def post_account(request: Request, payload: DriveAccountCreateIn, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    account = create_drive_account(db, **payload.model_dump())
    with transaction(db):
        audit.write_audit_log(db, actor_user_id=current.user.id, action='drive_account.create', target_type='drive_account', target_id=str(account.id), ip=request.client.host if request.client else None, user_agent=request.headers.get('user-agent'), success=True)
    db.refresh(account)
    return _out(account)


@router.patch('/{account_id}', response_model=DriveAccountOut, dependencies=[Depends(require_permissions(DRIVE_ACCOUNT_WRITE))])
def patch_account(request: Request, account_id: int, payload: DriveAccountUpdateIn, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    account = update_drive_account(db, account_id, **payload.model_dump(exclude_unset=True))
    with transaction(db):
        audit.write_audit_log(db, actor_user_id=current.user.id, action='drive_account.update', target_type='drive_account', target_id=str(account_id), ip=request.client.host if request.client else None, user_agent=request.headers.get('user-agent'), success=True)
    db.refresh(account)
    return _out(account)


@router.patch('/{account_id}/status', response_model=DriveAccountOut, dependencies=[Depends(require_permissions(DRIVE_ACCOUNT_WRITE))])
def patch_account_status(request: Request, account_id: int, payload: DriveAccountStatusIn, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    if payload.enabled:
        account = probe_drive_account(db, account_id)
        if account.runtime_status != "active":
            account.enabled = False
            with transaction(db):
                audit.write_audit_log(
                    db,
                    actor_user_id=current.user.id,
                    action="drive_account.status",
                    target_type="drive_account",
                    target_id=str(account_id),
                    ip=request.client.host if request.client else None,
                    user_agent=request.headers.get("user-agent"),
                    success=False,
                    detail=f"enabled=True probe=failed fail_count={getattr(account, 'probe_fail_count', 0) or 0} error={account.last_error or ''}",
                )
            db.refresh(account)
            raise bad_request("DRIVE_ACCOUNT_PROBE_FAILED", "账号探测失败，无法启用", detail=account.last_error or "驱动初始化失败")
        account.enabled = True
        account.probe_fail_count = 0
        with transaction(db):
            audit.write_audit_log(
                db,
                actor_user_id=current.user.id,
                action="drive_account.status",
                target_type="drive_account",
                target_id=str(account_id),
                ip=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
                success=True,
                detail="enabled=True probe=ok",
            )
        db.refresh(account)
        return _out(account)

    account = set_drive_account_enabled(db, account_id, False)
    with transaction(db):
        audit.write_audit_log(
            db,
            actor_user_id=current.user.id,
            action="drive_account.status",
            target_type="drive_account",
            target_id=str(account_id),
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            success=True,
            detail="enabled=False",
        )
    db.refresh(account)
    return _out(account)


@router.post('/{account_id}/default', response_model=DriveAccountOut, dependencies=[Depends(require_permissions(DRIVE_ACCOUNT_WRITE))])
def post_account_default(request: Request, account_id: int, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    account = set_default_drive_account(db, account_id)
    with transaction(db):
        audit.write_audit_log(db, actor_user_id=current.user.id, action='drive_account.default', target_type='drive_account', target_id=str(account_id), ip=request.client.host if request.client else None, user_agent=request.headers.get('user-agent'), success=True)
    db.refresh(account)
    return _out(account)


@router.delete('/{account_id}', dependencies=[Depends(require_permissions(DRIVE_ACCOUNT_WRITE))])
def delete_account(request: Request, account_id: int, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    delete_drive_account(db, account_id)
    with transaction(db):
        audit.write_audit_log(db, actor_user_id=current.user.id, action='drive_account.delete', target_type='drive_account', target_id=str(account_id), ip=request.client.host if request.client else None, user_agent=request.headers.get('user-agent'), success=True)
    return {'ok': True}


@router.post('/{account_id}/probe', response_model=DriveAccountOut, dependencies=[Depends(require_permissions(DRIVE_ACCOUNT_WRITE))])
def post_account_probe(request: Request, account_id: int, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    account = probe_drive_account(db, account_id)
    with transaction(db):
        audit.write_audit_log(db, actor_user_id=current.user.id, action='drive_account.probe', target_type='drive_account', target_id=str(account_id), ip=request.client.host if request.client else None, user_agent=request.headers.get('user-agent'), success=True)
    db.refresh(account)
    return _out(account)


@router.post('/{account_id}/auth/start', response_model=DriveAccountOut, dependencies=[Depends(require_permissions(DRIVE_ACCOUNT_WRITE))])
def post_account_auth_start(request: Request, account_id: int, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    account = probe_drive_account(db, account_id)
    with transaction(db):
        audit.write_audit_log(db, actor_user_id=current.user.id, action='drive_account.auth_start', target_type='drive_account', target_id=str(account_id), ip=request.client.host if request.client else None, user_agent=request.headers.get('user-agent'), success=True)
    db.refresh(account)
    return _out(account)


def _auth_payload(session):
    return {"account_id": session.account_id, "drive_type": session.drive_type,
            "method": session.method, "session_id": session.session_id, "payload": session.payload}


def _auth_audit(db, request, current, action, account_id):
    with transaction(db):
        audit.write_audit_log(db, actor_user_id=current.user.id, action=action,
            target_type='drive_account', target_id=str(account_id),
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get('user-agent'), success=True)


@router.post('/{account_id}/auth/qrcode/start', dependencies=[Depends(require_permissions(DRIVE_ACCOUNT_WRITE))])
def post_account_auth_qrcode_start(request: Request, account_id: int, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    session = drive_account_auth_use_case().start_qrcode(db, account_id)
    _auth_audit(db, request, current, 'drive_account.auth_qrcode_start', account_id)
    return _auth_payload(session)


@router.post('/auth/{session_id}/qrcode/poll', response_model=DriveAccountOut, dependencies=[Depends(require_permissions(DRIVE_ACCOUNT_WRITE))])
def post_account_auth_qrcode_poll(request: Request, session_id: str, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    result = drive_account_auth_use_case().poll_qrcode(db, session_id,
        persist=lambda account: _auth_audit(db, request, current, 'drive_account.auth_qrcode_confirm', account.id))
    if result.account is not None:
        db.refresh(result.account)
        return _out(result.account)
    _auth_audit(db, request, current, 'drive_account.auth_qrcode_poll', result.session.account_id)
    raise ApiError(code='DRIVE_ACCOUNT_AUTH_PENDING', message='扫码未完成', http_status=409,
        detail=json.dumps(_auth_payload(result.session), ensure_ascii=False))


def _submit_auth_code(db, request, current, session_id, code, method, action):
    result = drive_account_auth_use_case().submit_code(db, session_id, code, method=method,
        persist=lambda account: _auth_audit(db, request, current, action, account.id))
    if result.account is None:
        raise ApiError(code='DRIVE_ACCOUNT_AUTH_REQUIRED', message=result.message or '需要二次认证', http_status=409,
            detail=json.dumps(_auth_payload(result.session), ensure_ascii=False))
    db.refresh(result.account)
    return _out(result.account)


@router.post('/auth/{session_id}/captcha', response_model=DriveAccountOut, dependencies=[Depends(require_permissions(DRIVE_ACCOUNT_WRITE))])
def post_account_auth_captcha_submit(request: Request, session_id: str, payload: DriveAccountCaptchaSubmitIn, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    return _submit_auth_code(db, request, current, session_id, payload.code, 'captcha', 'drive_account.auth_captcha')


@router.post('/auth/{session_id}/sms/send', dependencies=[Depends(require_permissions(DRIVE_ACCOUNT_WRITE))])
def post_account_auth_sms_send(request: Request, session_id: str, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    use_case = drive_account_auth_use_case()
    session = use_case.status(session_id, 'sms')
    result = use_case.send_sms(db, session_id)
    _auth_audit(db, request, current, 'drive_account.auth_sms_send', session.account_id)
    return result


@router.post('/auth/{session_id}/sms/submit', response_model=DriveAccountOut, dependencies=[Depends(require_permissions(DRIVE_ACCOUNT_WRITE))])
def post_account_auth_sms_submit(request: Request, session_id: str, payload: DriveAccountSmsSubmitIn, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    return _submit_auth_code(db, request, current, session_id, payload.code, 'sms', 'drive_account.auth_sms_submit')


@router.post('/{account_id}/sign-in', dependencies=[Depends(require_permissions(DRIVE_ACCOUNT_WRITE))])
def post_account_sign_in(request: Request, account_id: int, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        result = sign_in_drive_account(db, account_id)
        with transaction(db):
            audit.write_audit_log(db, actor_user_id=current.user.id, action='drive_account.sign_in', target_type='drive_account', target_id=str(account_id), ip=request.client.host if request.client else None, user_agent=request.headers.get('user-agent'), success=True)
        return result
    except StaleDataError:
        raise
    except ApiError as e:
        db.rollback()
        if e.code == 'DRIVE_ACCOUNT_CONFLICT':
            raise
        with transaction(db):
            audit.write_audit_log(
                db,
                actor_user_id=current.user.id,
                action='drive_account.sign_in',
                target_type='drive_account',
                target_id=str(account_id),
                ip=request.client.host if request.client else None,
                user_agent=request.headers.get('user-agent'),
                success=False,
                detail=e.code,
            )
        raise
    except Exception:
        db.rollback()
        with transaction(db):
            audit.write_audit_log(
                db,
                actor_user_id=current.user.id,
                action='drive_account.sign_in',
                target_type='drive_account',
                target_id=str(account_id),
                ip=request.client.host if request.client else None,
                user_agent=request.headers.get('user-agent'),
                success=False,
                detail='DRIVE_ACCOUNT_SIGN_IN_FAILED',
            )
        raise ApiError(code='DRIVE_ACCOUNT_SIGN_IN_FAILED', message='签到失败，请检查登录状态或稍后重试', http_status=502) from None


@router.post('/refresh-profiles', response_model=list[DriveAccountOut], dependencies=[Depends(require_permissions(DRIVE_ACCOUNT_WRITE))])
def post_refresh_profiles(request: Request, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    items = refresh_drive_account_profiles(db)
    with transaction(db):
        audit.write_audit_log(db, actor_user_id=current.user.id, action='drive_account.refresh_profiles', target_type='drive_account', target_id='*', ip=request.client.host if request.client else None, user_agent=request.headers.get('user-agent'), success=True)
    for item in items:
        db.refresh(item)
    return [_out(item) for item in items]
