"""分享链接预览 / 验证相关端点（从 app.api.routes.tasks 拆分，路径与响应不变）。"""

from __future__ import annotations

import json
import os
import re

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.api.deps import require_permissions
from app.core.errors import bad_request, not_found
from app.core.permissions import TASK_READ
from app.application.container import commit, get_db
from app.application.container import adapter_factory as _acc_AdapterFactory; AdapterFactory = _acc_AdapterFactory()
from app.application.container import adapter_registry as _acc_AdapterRegistry; AdapterRegistry = _acc_AdapterRegistry()
from app.application.container import account_manager_cls as _acc_DatabaseAccountManager; DatabaseAccountManager = _acc_DatabaseAccountManager()
from app.application.container import magic_rename_cls as _acc_MagicRename; MagicRename = _acc_MagicRename()
from app.application.dto.task_browse import (
    SharePreviewBatchIn,
    SharePreviewBatchOut,
    SharePreviewIn,
    SharePreviewItemOut,
    SharePreviewOut,
    ShareValidateIn,
    ShareValidateOut,
)
from app.application.share_preview import preview_share
from app.application.share_preview_batch import preview_share_batch, validate_share_links
from app.application.tmdb_settings import get_or_create_tmdb_setting, get_tmdb_runtime_config
from app.application.drive_accounts import get_active_account, pick_default_account_name
from app.application.drive_payload import (
    bool_is_dir,
    pick_children_count,
    pick_fid,
    pick_fid_token,
    pick_name,
    pick_size,
    pick_updated_at,
)

router = APIRouter()


@router.post('/share/preview', response_model=SharePreviewOut, dependencies=[Depends(require_permissions(TASK_READ))])
def post_share_preview(payload: SharePreviewIn, db: Session = Depends(get_db)):
    return preview_share(payload, db)


@router.post("/share/preview-batch", response_model=SharePreviewBatchOut, dependencies=[Depends(require_permissions(TASK_READ))])
def post_share_preview_batch(payload: SharePreviewBatchIn, db: Session = Depends(get_db)):
    out, changed = preview_share_batch(db, payload)
    if changed:
        commit(db)
    return out


@router.post("/share/validate", response_model=ShareValidateOut, dependencies=[Depends(require_permissions(TASK_READ))])
def post_share_validate(payload: ShareValidateIn, db: Session = Depends(get_db)):
    """轻量级验证：只检查链接有效性 + 提取分享者信息"""
    return validate_share_links(db, payload.shareurls)


@router.post("/share/validate_stream", dependencies=[Depends(require_permissions(TASK_READ))])
def post_share_validate_stream(payload: ShareValidateIn, db: Session = Depends(get_db)):
    """流式验证：验证完一个链接立即返回结果"""
    from fastapi.responses import StreamingResponse
    from app.application.share_preview_batch import validate_share_links_streaming
    import json

    def event_generator():
        for item in validate_share_links_streaming(db, payload.shareurls):
            data = json.dumps({"shareurl": item.shareurl, "ok": item.ok, "share_author_name": item.share_author_name, "message": item.message}, ensure_ascii=False)
            yield f"data: {data}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
