from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from app.application.container import tasks_repo
from app.application.repository_records import TaskRecord

from app.application.task_share_links import _load_extra, _store_extra


def read_task_shareurl_update_logs(task: TaskRecord) -> list[dict[str, object]]:
    history = _load_extra(task).get('shareurl_update_logs')
    if not isinstance(history, list):
        return []

    records: list[dict[str, object]] = []
    for entry in history:
        if not isinstance(entry, dict):
            continue
        entry_id, at = entry.get('id'), entry.get('at')
        source, status = entry.get('source'), entry.get('status')
        if not isinstance(entry_id, str) or not entry_id or not isinstance(at, str) or not at:
            continue
        if source not in ('auto', 'manual') or status not in ('success', 'failed'):
            continue

        round_number = entry.get('round')
        records.append({
            'task_id': task.id,
            'taskname': task.taskname,
            'id': entry_id,
            'at': at,
            'source': source,
            'status': status,
            'round': round_number if isinstance(round_number, int) else None,
            **{
                key: entry[key] if isinstance(entry.get(key), str) else ''
                for key in ('message', 'old_shareurl', 'new_shareurl')
            },
        })
    return records


def clear_drama_shareurl_update_logs(db: Session) -> int:
    deleted = 0
    for task in tasks_repo().iter_drama_tasks_without_executions(db):
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
