from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.task import Task

from app.services.task_share_links import _load_extra, _store_extra


def clear_drama_shareurl_update_logs(db: Session) -> int:
    deleted = 0
    for task in db.scalars(select(Task).where(Task.task_type == 'drama')):
        extra = _load_extra(task)
        if 'shareurl_update_logs' not in extra:
            continue
        history = extra.pop('shareurl_update_logs')
        if isinstance(history, list):
            deleted += len(history)
        _store_extra(task, extra)
    db.flush()
    return deleted


def append_shareurl_update_log(
    task: object, *, source: str, status: str, message: str,
    old_shareurl: str = '', new_shareurl: str = '', round_number: int | None = None,
) -> dict:
    """保存最近 50 条换源结果，与任务执行成功/失败记录分开保留。"""
    extra = _load_extra(task)
    history = extra.get('shareurl_update_logs')
    history = [row for row in history if isinstance(row, dict)] if isinstance(history, list) else []
    entry = {
        'id': uuid4().hex,
        'at': datetime.now().astimezone().isoformat(timespec='milliseconds'),
        'source': source,
        'status': status,
        'message': message[:1000],
        'old_shareurl': old_shareurl,
        'new_shareurl': new_shareurl,
        'round': round_number,
    }
    extra['shareurl_update_logs'] = (history + [entry])[-50:]
    _store_extra(task, extra)
    return entry
