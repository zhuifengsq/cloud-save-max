from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.application.container import adapter_registry, tasks_repo
from app.core.errors import bad_request, not_found
from app.application.task_share_links import SHARE_LINK_KEY, drop_task_share_link, merge_task_extra
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


def create_task(db: Session, **payload) -> Any:
    drive_type = adapter_registry().detect_drive_type(payload['shareurl'])
    if drive_type is None:
        raise bad_request('TASK_SHAREURL_INVALID', '无法识别的网盘分享链接')
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
    previous_savepath = str(getattr(task, "savepath", "") or "").strip()
    previous_account_name = str(getattr(task, "account_name", "") or "").strip()
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
    if payload.get('extra') is not None:
        # 对外分享记录和换源日志由服务端维护，不能被旧编辑表单覆盖。
        merge_task_extra(task, {key: value for key, value in payload['extra'].items()
                                if key not in {'share_link', 'shareurl_update_logs'}})
    if any(key in payload for key in ('savepath', 'account_name')):
        link_target_changed = (
            str(getattr(task, "savepath", "") or "").strip() != previous_savepath
            or str(getattr(task, "account_name", "") or "").strip() != previous_account_name
        )
        if link_target_changed:
            # 对外分享指向保存目录，仅改变源链接不会改变它的目标。
            drop_task_share_link(task)
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