"""网盘文件浏览 / 新建目录 / 删除相关端点（从 app.api.routes.tasks 拆分，路径与响应不变）。"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from app.core.deps import CurrentUser, get_current_user, require_permissions
from app.core.errors import bad_request, not_found
from app.core.permissions import TASK_READ, TASK_WRITE
from app.db.session import commit, get_db
from app.extensions.runtime.adapter_registry import AdapterRegistry
from app.extensions.runtime.account_manager import DatabaseAccountManager
from app.schemas.task_browse import (
    DriveBrowseIn,
    DriveDeleteIn,
    DriveDeleteOut,
    DriveBrowseItemOut,
    DriveBrowseOut,
    DriveBrowsePathOut,
    DriveMkdirIn,
    DriveMkdirOut,
)
from app.services import audit
from app.services.drive_accounts import get_active_account, pick_any_default_account, pick_default_account_name
from app.services.drive_payload import bool_is_dir, pick_children_count, pick_fid, pick_name, pick_size, pick_updated_at

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post('/drive/browse', response_model=DriveBrowseOut, dependencies=[Depends(require_permissions(TASK_READ))])
def post_drive_browse(payload: DriveBrowseIn, db: Session = Depends(get_db)):
    drive_type: str | None = None
    if payload.account_name:
        account = get_active_account(db, payload.account_name)
        if account is None:
            raise not_found("DRIVE_ACCOUNT_NOT_FOUND", "指定账号不存在或不可用")
        drive_type = str(account.drive_type)
    if not payload.account_name:
        if payload.shareurl:
            drive_type = AdapterRegistry.detect_drive_type(payload.shareurl)
            if drive_type is None:
                raise bad_request('TASK_SHAREURL_INVALID', '无法识别的网盘分享链接')
            payload.account_name = pick_default_account_name(db, drive_type)
        else:
            any_default = pick_any_default_account(db)
            if any_default:
                payload.account_name = any_default.name
                drive_type = str(any_default.drive_type)
    if not payload.account_name:
        raise not_found('DRIVE_ACCOUNT_NOT_FOUND', '没有可用的驱动账号')
    manager = DatabaseAccountManager(db)
    task_payload = {"shareurl": payload.shareurl or "", "account_name": payload.account_name}
    manager.init_for_tasks([task_payload])
    adapter = manager.get_adapter_for_task(task_payload)
    if adapter is None:
        raise not_found('DRIVE_ACCOUNT_NOT_FOUND', '没有可用的驱动账号')
    dir_path = str(payload.dir_path or "").strip() or "/"

    is_fid_mode = ("/" not in dir_path) and (dir_path not in ("/", "0"))
    normalized_path = re.sub(r"/+", "/", dir_path)
    if not normalized_path.startswith("/") and not is_fid_mode:
        normalized_path = "/" + normalized_path
    normalized_path = normalized_path.rstrip('/')
    paths: list[DriveBrowsePathOut] = []
    if dir_path in ("/", "0"):
        pdir_fid = "0"
    elif is_fid_mode:
        pdir_fid = dir_path
    else:
        fid_list = adapter.get_fids([normalized_path])
        match = None
        for item in fid_list or []:
            item_path = item.get("file_path") or item.get("path") or item.get("filePath")
            if item_path == normalized_path:
                match = item
                break
        if match is None and fid_list:
            match = fid_list[0]
        pdir_fid = str(match.get("fid")) if match and match.get("fid") else None

        segments = [s for s in normalized_path.split("/") if s]
        if segments:
            accum_paths = ["/" + "/".join(segments[: i + 1]) for i in range(len(segments))]
            fid_arr = adapter.get_fids(accum_paths) or []
            fid_map: dict[str, str] = {}
            for it in fid_arr:
                p = it.get("file_path") or it.get("path") or it.get("filePath")
                f = it.get("fid")
                if p and f:
                    fid_map[str(p)] = str(f)
            for i, name in enumerate(segments):
                p = accum_paths[i]
                fid_val = fid_map.get(p)
                if fid_val:
                    paths.append(DriveBrowsePathOut(fid=fid_val, name=name))

    if not pdir_fid:
        return DriveBrowseOut(
            account_name=str(payload.account_name),
            drive_type=str(drive_type) if drive_type else None,
            dir_path=dir_path,
            exists=False,
            pdir_fid=None,
            items=[],
            paths=paths,
        )

    listing = adapter.ls_dir(str(pdir_fid), max_items=payload.max_items)
    raw_items = (((listing or {}).get("data") or {}).get("list")) or []
    items: list[DriveBrowseItemOut] = []
    for raw in raw_items[: payload.max_items]:
        fid = pick_fid(raw)
        name = pick_name(raw)
        if not fid or not name:
            continue
        is_dir = bool_is_dir(raw)
        items.append(
            DriveBrowseItemOut(
                fid=fid,
                name=name,
                is_dir=is_dir,
                updated_at=pick_updated_at(raw),
                size=pick_size(raw),
                include_items=pick_children_count(raw) if is_dir else None,
                file_name=name,
                dir=is_dir,
            )
        )
    return DriveBrowseOut(
        account_name=str(payload.account_name),
        drive_type=str(drive_type) if drive_type else None,
        dir_path=dir_path,
        exists=True,
        pdir_fid=str(pdir_fid),
        items=items,
        paths=paths,
    )


@router.post('/drive/mkdir', response_model=DriveMkdirOut, dependencies=[Depends(require_permissions(TASK_WRITE))])
def post_drive_mkdir(payload: DriveMkdirIn, db: Session = Depends(get_db)):
    if payload.account_name and get_active_account(db, payload.account_name) is None:
        raise not_found("DRIVE_ACCOUNT_NOT_FOUND", "指定账号不存在或不可用")
    if not payload.account_name:
        if not payload.shareurl:
            raise bad_request('TASK_ACCOUNT_REQUIRED', '缺少 account_name 或 shareurl')
        drive_type = AdapterRegistry.detect_drive_type(payload.shareurl)
        if drive_type is None:
            raise bad_request('TASK_SHAREURL_INVALID', '无法识别的网盘分享链接')
        payload.account_name = pick_default_account_name(db, drive_type)
    if not payload.account_name:
        raise not_found('DRIVE_ACCOUNT_NOT_FOUND', '没有可用的驱动账号')
    manager = DatabaseAccountManager(db)
    task_payload = {"shareurl": payload.shareurl or "", "account_name": payload.account_name}
    manager.init_for_tasks([task_payload])
    adapter = manager.get_adapter_for_task(task_payload)
    if adapter is None:
        raise not_found('DRIVE_ACCOUNT_NOT_FOUND', '没有可用的驱动账号')
    response = adapter.mkdir(payload.dir_path)
    return DriveMkdirOut(account_name=str(payload.account_name), dir_path=payload.dir_path, response=response or {})


@router.post('/drive/delete', response_model=DriveDeleteOut, dependencies=[Depends(require_permissions(TASK_WRITE))])
def post_drive_delete(
    payload: DriveDeleteIn,
    request: Request,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DriveDeleteOut:
    account_name = str(payload.account_name or "").strip()
    drive_type: str | None = None
    if account_name:
        account = get_active_account(db, account_name)
        if account is None:
            raise not_found('DRIVE_ACCOUNT_NOT_FOUND', '指定账号不存在或不可用')
        drive_type = str(account.drive_type)
    elif payload.shareurl:
        drive_type = AdapterRegistry.detect_drive_type(payload.shareurl)
        if not drive_type:
            raise bad_request('TASK_SHAREURL_INVALID', '无法识别链接所属网盘')
        account_name = pick_default_account_name(db, drive_type)
        account = get_active_account(db, account_name) if account_name else None
        if account is None:
            raise not_found('DRIVE_ACCOUNT_NOT_FOUND', '没有可用的网盘账号')
    else:
        any_default = pick_any_default_account(db)
        if any_default is None:
            raise not_found('DRIVE_ACCOUNT_NOT_FOUND', '没有可用的网盘账号')
        account_name = any_default.name
        drive_type = str(any_default.drive_type)

    fids = [str(x).strip() for x in (payload.fids or []) if str(x).strip()]
    if not fids:
        raise bad_request('DRIVE_DELETE_EMPTY', '缺少要删除的文件')

    manager = DatabaseAccountManager(db)
    task_payload = {'shareurl': payload.shareurl or '', 'account_name': account_name}
    manager.init_for_tasks([task_payload])
    adapter = manager.get_adapter_for_task(task_payload)
    if adapter is None:
        raise not_found('DRIVE_ACCOUNT_NOT_FOUND', '没有可用的网盘账号')

    try:
        result = adapter.delete(fids)
    except NotImplementedError as exc:
        raise bad_request('DRIVE_DELETE_UNSUPPORTED', f'{drive_type or ""} 暂不支持删除') from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception('删除网盘文件失败 account=%s', account_name)
        raise bad_request('DRIVE_DELETE_FAILED', str(exc) or '删除失败') from exc

    if isinstance(result, dict):
        code = result.get('code')
        if code is not None:
            ok = code in (0, '0')
        else:
            ok = bool(result.get('success'))
        message = str(result.get('message') or '')
    else:
        ok = False
        message = ''

    audit.write_audit_log(
        db,
        actor_user_id=current.user.id,
        action='task.drive.delete',
        target_type='drive_file',
        target_id=account_name,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get('user-agent'),
        success=bool(ok),
        detail=f'drive_type={drive_type or ""} count={len(fids)}',
    )
    commit(db)
    return DriveDeleteOut(ok=bool(ok), message=message or None, deleted=len(fids) if ok else 0)
