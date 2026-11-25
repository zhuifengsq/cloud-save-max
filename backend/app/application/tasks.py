from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.application.container import adapter_registry, tasks_repo
from app.core.errors import ApiError, bad_request, not_found
from app.application.task_share_links import (
    SHARE_LINK_KEY, _load_extra, _store_extra, drop_task_share_link, merge_task_extra, normalize_savepath,
)
from app.application.shareurl_update_logs import append_shareurl_update_log

logger = logging.getLogger(__name__)


def list_tasks(db: Session) -> list[Any]:
    # 不再 eager loading 执行记录：需要最近几条请用 list_tasks_recent_executions 显式装配。
    return tasks_repo().list_tasks(db)


def list_tasks_recent_executions(db: Session, *, limit: int = 3) -> list[Any]:
    # 显式 noload：列表只需要最近 N 条执行记录，仓储里手动装配，绝不能全量加载。
    return tasks_repo().list_tasks_recent_executions(db, limit=limit)


def get_task(db: Session, task_id: int) -> Any:
    # 只取任务本身：调用方（执行、改状态、删除、换链）都不需要执行记录。
    # 需要执行记录时请用 list_recent_executions 显式查询，避免全量加载 run_log。
    task = tasks_repo().get_task_row(db, task_id)
    if task is None:
        raise not_found('TASK_NOT_FOUND', '任务不存在')
    return task


def list_recent_executions(db: Session, task_id: int, *, limit: int = 3) -> list[Any]:
    """按开始时间倒序取某任务最近 N 条执行记录（替代 relationship 懒加载/全量 eager loading）。"""
    return tasks_repo().list_recent_executions(db, task_id, limit=limit)


def _validate_shareurl(shareurl: str) -> None:
    if not shareurl or adapter_registry().detect_drive_type(shareurl) is None:
        raise bad_request('TASK_SHAREURL_INVALID', '无法识别的网盘分享链接')


def _save_target(task: Any) -> tuple[str, str, str | None]:
    account = str(getattr(task, "account_name", "") or "").strip()
    shareurl = str(getattr(task, "shareurl", "") or "").strip()
    implicit_drive = adapter_registry().detect_drive_type(shareurl) if not account else None
    return normalize_savepath(getattr(task, "savepath", None)), account, implicit_drive


def create_task(db: Session, **payload) -> Any:
    _validate_shareurl(payload['shareurl'])
    tmdb_media_type = payload.get("tmdb_media_type")
    if tmdb_media_type is not None:
        tmdb_media_type = str(tmdb_media_type).strip().lower() or None
        if tmdb_media_type is not None and tmdb_media_type not in ("movie", "tv"):
            raise bad_request("TASK_TMDB_MEDIA_TYPE_INVALID", "无效的 TMDB 类型")
    tmdb_id = payload.get("tmdb_id")
    if tmdb_id is not None:
        try:
            tmdb_id = int(tmdb_id)
        except Exception:
            raise bad_request("TASK_TMDB_ID_INVALID", "无效的 TMDB ID")
        if tmdb_id <= 0:
            raise bad_request("TASK_TMDB_ID_INVALID", "无效的 TMDB ID")
    return tasks_repo().create_task_row(
        db,
        task_uid=payload.get('task_uid') or uuid.uuid4().hex,
        task_type=payload.get('task_type') or 'generic',
        taskname=payload['taskname'],
        shareurl=payload['shareurl'],
        savepath=payload['savepath'],
        pattern=payload.get('pattern'),
        replace=payload.get('replace'),
        ignore_extension=payload.get('ignore_extension', False),
        account_name=payload.get('account_name'),
        tmdb_id=tmdb_id,
        tmdb_media_type=tmdb_media_type,
        enabled=payload.get('enabled', True),
        addition_json=json.dumps(payload.get('addition') or {}, ensure_ascii=False),
        extra_json=json.dumps({k: v for k, v in (payload.get('extra') or {}).items() if k != SHARE_LINK_KEY}, ensure_ascii=False),
    )


def update_task(db: Session, task_id: int, **payload) -> Any:
    task = get_task(db, task_id)
    previous_shareurl = str(getattr(task, "shareurl", "") or "").strip()
    if payload.get("shareurl") is not None:
        shareurl = str(payload["shareurl"]).strip()
        if not shareurl or shareurl != previous_shareurl:
            _validate_shareurl(shareurl)
        payload["shareurl"] = shareurl
    previous_target = _save_target(task) if {"savepath", "account_name", "shareurl"} & payload.keys() else None
    requested_enabled = payload.get("enabled")
    expected_enabled = payload.get("expected_enabled")
    if getattr(task, "task_type", None) == "drama" and requested_enabled is not None:
        status_changed = requested_enabled != task.enabled
        mixed_edit = any(key not in {"enabled", "expected_enabled"} for key in payload)
        if (expected_enabled is not None and expected_enabled != task.enabled) or (
            expected_enabled is None and status_changed and mixed_edit
        ):
            raise ApiError("TASK_STATUS_CHANGED", "任务启用状态可能已变更，请刷新后重新操作", 409)
    editable_extra = {
        key: value for key, value in (payload.get("extra") or {}).items()
        if key not in {SHARE_LINK_KEY, "shareurl_update_logs", "saved_latest"}
    }
    if getattr(task, "task_type", None) == "drama" and (
        editable_extra or any(key in payload for key in ("enabled", "shareurl", "savepath", "account_name"))
    ):
        if not tasks_repo().guard_task_edit(db, task):
            raise ApiError("TASK_CHANGED", "任务已变更，请刷新后重新保存", 409)
    clearable_fields = {'pattern', 'replace', 'account_name', 'tmdb_id', 'tmdb_media_type'}
    for key in [
        'task_type',
        'taskname',
        'shareurl',
        'savepath',
        'pattern',
        'replace',
        'ignore_extension',
        'account_name',
        'tmdb_id',
        'tmdb_media_type',
        'enabled',
    ]:
        if key not in payload:
            continue
        if key in clearable_fields:
            value = payload[key]
            if key == "tmdb_media_type":
                if isinstance(value, str) and not value.strip():
                    value = None
                if value is not None:
                    value = str(value).strip().lower() or None
                if value is not None and value not in ("movie", "tv"):
                    raise bad_request("TASK_TMDB_MEDIA_TYPE_INVALID", "无效的 TMDB 类型")
            if key == "tmdb_id":
                if value in ("", 0):
                    value = None
                if value is not None:
                    try:
                        value = int(value)
                    except Exception:
                        raise bad_request("TASK_TMDB_ID_INVALID", "无效的 TMDB ID")
                    if value <= 0:
                        raise bad_request("TASK_TMDB_ID_INVALID", "无效的 TMDB ID")
            if isinstance(value, str) and not value.strip():
                value = None
            setattr(task, key, value)
            continue
        if payload[key] is not None:
            setattr(task, key, payload[key])
    if payload.get('addition') is not None:
        task.addition_json = json.dumps(payload['addition'], ensure_ascii=False)
    if editable_extra:
        # 运行进度、对外分享和换源日志由服务端维护。
        merge_task_extra(task, editable_extra)
    if previous_target is not None and _save_target(task) != previous_target:
        drop_task_share_link(task)
        if getattr(task, "task_type", None) == "drama":
            extra = _load_extra(task)
            if "saved_latest" in extra:
                del extra["saved_latest"]
                _store_extra(task, extra)
    if "shareurl" in payload:
        new_shareurl = str(getattr(task, "shareurl", "") or "").strip()
        if new_shareurl != previous_shareurl:
            append_shareurl_update_log(
                task, source='manual', status='success', message='已手动更换源链接',
                old_shareurl=previous_shareurl, new_shareurl=new_shareurl,
            )

    db.flush()
    return task


def set_task_enabled(db: Session, task_id: int, enabled: bool) -> Any:
    task = get_task(db, task_id)
    task.enabled = enabled
    return task


def delete_task(db: Session, task_id: int) -> None:
    task = get_task(db, task_id)
    tasks_repo().delete_task_row(db, task)
