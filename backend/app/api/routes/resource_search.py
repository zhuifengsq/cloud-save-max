from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user, require_permissions
from app.core.errors import forbidden
from app.core.permissions import PLUGIN_READ, PLUGIN_WRITE, TASK_READ
from app.db.session import get_db
from app.services.api_search_sources import (
    create_api_source,
    delete_api_source,
    get_api_source,
    list_api_sources,
    search_api_source,
    update_api_source,
)
from app.services.resource_search import cache_clear, get_quark_share_author


router = APIRouter()


class ShareAuthorOut(BaseModel):
    author_name: str = ""


class ApiSourceCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    enabled: bool = True
    method: str = "GET"
    url: str = Field(min_length=1, max_length=2048)
    headers: dict[str, Any] = Field(default_factory=dict)
    query_params: dict[str, Any] = Field(default_factory=dict)
    body: dict[str, Any] = Field(default_factory=dict)
    response_rule: str = Field(default="", max_length=2048)
    items_path: str = "data.items"
    title_path: str = "title"
    url_path: str = "url"
    password_path: str = "password"
    datetime_path: str = "datetime"


class ApiSourceUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None
    method: str | None = None
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    headers: dict[str, Any] | None = None
    query_params: dict[str, Any] | None = None
    body: dict[str, Any] | None = None
    response_rule: str | None = Field(default=None, max_length=2048)
    items_path: str | None = None
    title_path: str | None = None
    url_path: str | None = None
    password_path: str | None = None
    datetime_path: str | None = None


class SourceTestIn(BaseModel):
    keyword: str = Field(min_length=2, max_length=100)


def _require_admin(current: CurrentUser) -> None:
    if "admin" not in current.roles:
        raise forbidden("SEARCH_API_ADMIN_REQUIRED", "仅管理员可以管理 API 搜索源")


@router.get("/api-sources", dependencies=[Depends(require_permissions(PLUGIN_READ))])
def get_api_sources(current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    _require_admin(current)
    return list_api_sources(db)


@router.post("/api-sources", dependencies=[Depends(require_permissions(PLUGIN_WRITE))])
def post_api_source(payload: ApiSourceCreateIn, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    _require_admin(current)
    result = create_api_source(db, payload.model_dump())
    db.commit()
    cache_clear()
    return result


@router.patch("/api-sources/{source_id:int}", dependencies=[Depends(require_permissions(PLUGIN_WRITE))])
def patch_api_source(source_id: int, payload: ApiSourceUpdateIn, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    _require_admin(current)
    result = update_api_source(db, source_id, payload.model_dump(exclude_unset=True))
    db.commit()
    cache_clear()
    return result


@router.delete("/api-sources/{source_id:int}", dependencies=[Depends(require_permissions(PLUGIN_WRITE))])
def remove_api_source(source_id: int, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, bool]:
    _require_admin(current)
    delete_api_source(db, source_id)
    db.commit()
    cache_clear()
    return {"success": True}


@router.post("/api-sources/{source_id:int}/test", dependencies=[Depends(require_permissions(PLUGIN_WRITE))])
def test_api_source(source_id: int, payload: SourceTestIn, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    _require_admin(current)
    row = get_api_source(db, source_id)
    config = {item["id"]: item for item in list_api_sources(db)}[row.id]
    start = time.monotonic()
    try:
        results = search_api_source(config, payload.keyword)
        return {"success": True, "results": results[:20], "count": len(results), "latency_ms": round((time.monotonic() - start) * 1000)}
    except Exception as exc:
        return {"success": False, "results": [], "count": 0, "latency_ms": round((time.monotonic() - start) * 1000), "error": str(exc)[:300]}


@router.get("/share-author", response_model=ShareAuthorOut, dependencies=[Depends(require_permissions(TASK_READ))])
def get_share_author(shareurl: str = Query("", description="分享链接")) -> ShareAuthorOut:
    """获取夸克分享链接的分享者昵称"""
    author = get_quark_share_author(shareurl) or ""
    return ShareAuthorOut(author_name=author)

