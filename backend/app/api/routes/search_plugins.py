from __future__ import annotations

from fastapi import APIRouter, Depends, File, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user, require_permissions
from app.core.errors import forbidden
from app.core.permissions import PLUGIN_READ, PLUGIN_WRITE
from app.db.session import get_db
from app.services import audit
from app.services.resource_search import cache_clear
from app.services.search_plugins import (
    MAX_PLUGIN_BYTES,
    list_search_plugins,
    set_search_plugin_enabled,
    test_search_plugin,
    upload_search_plugin,
)


router = APIRouter()


class SearchPluginUpdateIn(BaseModel):
    enabled: bool


class SearchPluginTestIn(BaseModel):
    keyword: str = Field(min_length=2, max_length=100)


def _require_admin(current: CurrentUser) -> None:
    if "admin" not in current.roles:
        raise forbidden("SEARCH_PLUGIN_ADMIN_REQUIRED", "仅管理员可以管理 Python 搜索插件")


def _audit(db: Session, request: Request, current: CurrentUser, action: str, key: str) -> None:
    audit.write_audit_log(
        db,
        actor_user_id=current.user.id,
        action=action,
        target_type="search_plugin",
        target_id=key,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        success=True,
    )


@router.get("", dependencies=[Depends(require_permissions(PLUGIN_READ))])
def get_search_plugins(current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_admin(current)
    return {"plugins": list_search_plugins(db)}


@router.post("/refresh", dependencies=[Depends(require_permissions(PLUGIN_WRITE))])
def refresh_search_plugins(request: Request, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_admin(current)
    plugins = list_search_plugins(db)
    _audit(db, request, current, "search_plugin.refresh", "*")
    db.commit()
    cache_clear()
    return {"plugins": plugins}


@router.post("/upload", dependencies=[Depends(require_permissions(PLUGIN_WRITE))])
async def post_upload_search_plugin(
    request: Request,
    file: UploadFile = File(...),
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin(current)
    data = await file.read(MAX_PLUGIN_BYTES + 1)
    try:
        uploaded = upload_search_plugin(file.filename or "", data)
    finally:
        await file.close()
    plugins = list_search_plugins(db)
    _audit(db, request, current, "search_plugin.upload", uploaded["key"])
    db.commit()
    cache_clear()
    return {"uploaded": uploaded, "plugins": plugins}


@router.patch("/{key}", dependencies=[Depends(require_permissions(PLUGIN_WRITE))])
def patch_search_plugin(
    request: Request,
    key: str,
    payload: SearchPluginUpdateIn,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin(current)
    item = set_search_plugin_enabled(db, key, payload.enabled)
    _audit(db, request, current, "search_plugin.update", key)
    db.commit()
    cache_clear()
    return item


@router.post("/{key}/test", dependencies=[Depends(require_permissions(PLUGIN_WRITE))])
def post_test_search_plugin(
    key: str,
    payload: SearchPluginTestIn,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin(current)
    return test_search_plugin(db, key, payload.keyword)
