from __future__ import annotations

from fastapi import APIRouter, Depends, File, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user, require_permissions
from app.core.errors import forbidden
from app.core.permissions import PLUGIN_READ, PLUGIN_WRITE
from app.db.session import get_db, transaction, transaction
from app.application import audit
from app.application.resource_search import cache_clear
from app.application.search_plugins import (
    MAX_PLUGIN_BYTES,
    list_search_plugins,
    set_search_plugin_enabled,
    test_search_plugin,
    upload_search_plugin,
    save_search_plugin_config,
    clear_search_plugin_cache,
)


router = APIRouter()


class SearchPluginUpdateIn(BaseModel):
    enabled: bool


class SearchPluginTestIn(BaseModel):
    keyword: str = Field(min_length=2, max_length=100)


class SearchPluginConfigIn(BaseModel):
    config: dict


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
    with transaction(db):
        _audit(db, request, current, "search_plugin.refresh", "*")
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
    with transaction(db):
        _audit(db, request, current, "search_plugin.upload", uploaded["key"])
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
    with transaction(db):
        item = set_search_plugin_enabled(db, key, payload.enabled)
        _audit(db, request, current, "search_plugin.update", key)
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


@router.put('/{key}/config', dependencies=[Depends(require_permissions(PLUGIN_WRITE))])
def put_search_plugin_config(request: Request, key: str, payload: SearchPluginConfigIn,
                             current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_admin(current)
    with transaction(db):
        item = save_search_plugin_config(db, key, payload.config)
        _audit(db, request, current, 'search_plugin.config', key)
    cache_clear()
    return item


@router.post('/{key}/clear-cache', dependencies=[Depends(require_permissions(PLUGIN_WRITE))])
def post_clear_search_plugin_cache(request: Request, key: str,
                                   current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_admin(current)
    with transaction(db):
        result = clear_search_plugin_cache(db, key)
        _audit(db, request, current, 'search_plugin.clear_cache', key)
    cache_clear()
    return result
