import requests
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.deps import require_permissions
from app.core.errors import ApiError, bad_request
from app.core.permissions import TASK_READ
from app.application.wps_document_search import clear_hot_updates_cache, read_hot_updates


router = APIRouter()


class WpsHotUpdatesIn(BaseModel):
    document_url: str = Field(min_length=1, max_length=2048)


@router.post('/wps-hot-updates', dependencies=[Depends(require_permissions(TASK_READ))])
def post_wps_hot_updates(payload: WpsHotUpdatesIn):
    try:
        return read_hot_updates(payload.document_url)
    except requests.RequestException as exc:
        raise ApiError('WPS_READ_FAILED', '读取 WPS 文档失败，请检查网络或分享链接后重试', 502) from exc
    except ValueError as exc:
        raise bad_request('WPS_IMPORT_INVALID', str(exc)) from exc


@router.post('/wps-hot-updates/clear-cache', status_code=204, dependencies=[Depends(require_permissions(TASK_READ))])
def post_clear_wps_hot_updates_cache(payload: WpsHotUpdatesIn):
    try:
        clear_hot_updates_cache(payload.document_url)
    except ValueError as exc:
        raise bad_request('WPS_IMPORT_INVALID', str(exc)) from exc
