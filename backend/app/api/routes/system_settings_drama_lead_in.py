"""追剧任务引流文件配置端点（挂载在 /system-settings 前缀下）。

契约要求：
1. drives 必须遍历适配器注册表动态生成，未声明能力的网盘也返回 supported=false；
2. GET 脱敏，永不返回提取码与完整分享链接；
3. 保存前必须用适配器真实解析素材，失败时返回 400 与中文原因。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user, require_permissions
from app.core.permissions import TASK_READ, TASK_WRITE
from app.db.session import commit, get_db
from app.schemas.drama_lead_in import (
    DramaLeadInOwnDirIn,
    DramaLeadInOwnDirOut,
    DramaLeadInOwnFileResolveIn,
    DramaLeadInOwnFileResolveOut,
    DramaLeadInScanIn,
    DramaLeadInScanOut,
    DramaLeadInSettingsOut,
    DramaLeadInSettingsUpdateIn,
)
from app.application import audit
from app.application.drama_lead_in import (
    build_settings_payload,
    list_drama_lead_in_own_dir,
    resolve_drama_lead_in_own_file,
    scan_drama_lead_in_material,
    update_drama_lead_in_settings,
)

router = APIRouter()


@router.get("/drama-lead-in", response_model=DramaLeadInSettingsOut, dependencies=[Depends(require_permissions(TASK_READ))])
def get_drama_lead_in_settings(db: Session = Depends(get_db)) -> DramaLeadInSettingsOut:
    return DramaLeadInSettingsOut(**build_settings_payload(db))


@router.put("/drama-lead-in", response_model=DramaLeadInSettingsOut, dependencies=[Depends(require_permissions(TASK_WRITE))])
def put_drama_lead_in_settings(
    payload: DramaLeadInSettingsUpdateIn,
    request: Request,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DramaLeadInSettingsOut:
    result = update_drama_lead_in_settings(db, payload.model_dump())
    audit.write_audit_log(
        db,
        actor_user_id=current.user.id,
        action="system_setting.drama_lead_in_update",
        target_type="system_setting",
        target_id="drama_lead_in",
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        success=True,
        detail=json.dumps(
            {
                "enabled": bool(result.get("enabled")),
                "drives": [item.get("drive_type") for item in result.get("drives") or [] if item.get("configured")],
            },
            ensure_ascii=False,
        )[:1024],
    )
    commit(db)
    return DramaLeadInSettingsOut(**result)


@router.post(
    "/drama-lead-in/own-file/resolve",
    response_model=DramaLeadInOwnFileResolveOut,
    dependencies=[Depends(require_permissions(TASK_WRITE))],
)
def post_drama_lead_in_own_file_resolve(
    payload: DramaLeadInOwnFileResolveIn,
    request: Request,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DramaLeadInOwnFileResolveOut:
    """解析本账号文件：按账号 + 完整路径实时定位文件，供设置界面确认素材。"""
    result = resolve_drama_lead_in_own_file(db, payload.drive_type, payload.account_name, payload.file_path)
    audit.write_audit_log(
        db,
        actor_user_id=current.user.id,
        action="system_setting.drama_lead_in_own_file_resolve",
        target_type="system_setting",
        target_id="drama_lead_in",
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        success=True,
        # 审计只记录网盘与账号，不记录账号内文件路径。
        detail=json.dumps(
            {"drive_type": payload.drive_type, "account_name": payload.account_name},
            ensure_ascii=False,
        )[:1024],
    )
    commit(db)
    return DramaLeadInOwnFileResolveOut(**result)


@router.post(
    "/drama-lead-in/own-file/list",
    response_model=DramaLeadInOwnDirOut,
    dependencies=[Depends(require_permissions(TASK_WRITE))],
)
def post_drama_lead_in_own_file_list(
    payload: DramaLeadInOwnDirIn,
    request: Request,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DramaLeadInOwnDirOut:
    """浏览本账号目录（只读），供设置界面逐级选择素材文件。"""
    result = list_drama_lead_in_own_dir(
        db, payload.drive_type, payload.account_name, payload.dir_path
    )
    audit.write_audit_log(
        db,
        actor_user_id=current.user.id,
        action="system_setting.drama_lead_in_own_dir_list",
        target_type="system_setting",
        target_id="drama_lead_in",
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        success=True,
        # 审计只记录网盘、账号与条目数，不记录目录路径与文件名清单。
        detail=json.dumps(
            {
                "drive_type": payload.drive_type,
                "account_name": payload.account_name,
                "entry_count": len(result.get("entries") or []),
            },
            ensure_ascii=False,
        )[:1024],
    )
    commit(db)
    return DramaLeadInOwnDirOut(**result)


@router.post("/drama-lead-in/scan", response_model=DramaLeadInScanOut, dependencies=[Depends(require_permissions(TASK_WRITE))])
def post_drama_lead_in_scan(
    payload: DramaLeadInScanIn,
    request: Request,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DramaLeadInScanOut:
    result = scan_drama_lead_in_material(db, payload.drive_type, payload.shareurl, payload.passcode)
    audit.write_audit_log(
        db,
        actor_user_id=current.user.id,
        action="system_setting.drama_lead_in_scan",
        target_type="system_setting",
        target_id="drama_lead_in",
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        success=True,
        detail=json.dumps(
            {"drive_type": payload.drive_type, "file_count": len(result.get("files") or [])},
            ensure_ascii=False,
        )[:1024],
    )
    commit(db)
    return DramaLeadInScanOut(**result)
