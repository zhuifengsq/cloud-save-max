"""任务相关 ORM 对象 → Pydantic 响应模型（路由层不再拼装业务字段）。

task_out 里的 executions 必须显式传入或已装配：模型级 eager loading 已在 P0-1 移除，
未传 executions 时读取 relationship 只拿到已加载集合，不会触发懒加载查询。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.schemas.task import TaskExecutionOut, TaskOut
from app.schemas.task_scheduler import TaskSchedulerSettingOut
from app.services.drama_update_progress import build_drama_update_progress
from app.services.tmdb_view import get_tmdb_status, tmdb_cache_key


def execution_out(item) -> TaskExecutionOut:
    return TaskExecutionOut(
        id=item.id,
        task_id=item.task_id,
        status=item.status,
        started_at=item.started_at,
        finished_at=item.finished_at,
        tree_summary=item.tree_summary,
        message=item.message,
        stage=getattr(item, "stage", None),
        run_log=getattr(item, "run_log", None),
        adapter_snapshot=json.loads(item.adapter_snapshot) if item.adapter_snapshot else {},
    )


def task_out(
    db: Session,
    item,
    *,
    tmdb_status_map: dict[tuple[str, int], str | None] | None = None,
    tmdb_payload_map: dict[tuple[str, int], dict[str, object] | None] | None = None,
    executions: list | None = None,
) -> TaskOut:
    # executions 显式传入时不再碰 relationship；未传时才读取已装配的集合，避免触发全量懒加载。
    raw_executions = list(executions) if executions is not None else list(getattr(item, "executions", None) or [])
    raw_executions.sort(key=lambda x: x.started_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    tmdb_status: str | None = None
    tmdb_is_ended: bool | None = None
    drama_update_progress = None
    key = tmdb_cache_key(item)
    if key is not None:
        tmdb_status = tmdb_status_map.get(key) if isinstance(tmdb_status_map, dict) else get_tmdb_status(db, key[0], key[1])
        if key[0] == "tv" and tmdb_status is not None:
            tmdb_is_ended = tmdb_status in ("Ended", "Canceled")
    extra = json.loads(item.extra_json) if item.extra_json else {}
    if str(getattr(item, "task_type", "") or "") == "drama":
        # 进度来自 TMDB 最新已播 + 任务在执行时记录的 saved_latest（不再读快照表）
        saved_latest = extra.get("saved_latest") if isinstance(extra, dict) else None
        tmdb_details = tmdb_payload_map.get(key) if (isinstance(tmdb_payload_map, dict) and key is not None and key[0] == "tv") else None
        drama_update_progress = build_drama_update_progress(tmdb_details=tmdb_details, saved_latest=saved_latest)

    return TaskOut(
        id=item.id,
        task_uid=item.task_uid,
        task_type=item.task_type,
        taskname=item.taskname,
        shareurl=item.shareurl,
        savepath=item.savepath,
        pattern=item.pattern,
        replace=item.replace,
        ignore_extension=item.ignore_extension,
        account_name=item.account_name,
        tmdb_id=getattr(item, "tmdb_id", None),
        tmdb_media_type=getattr(item, "tmdb_media_type", None),
        tmdb_status=tmdb_status,
        tmdb_is_ended=tmdb_is_ended,
        drama_update_progress=drama_update_progress,
        enabled=item.enabled,
        addition=json.loads(item.addition_json) if item.addition_json else {},
        extra=json.loads(item.extra_json) if item.extra_json else {},
        executions=[execution_out(x) for x in raw_executions[:3]],
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def scheduler_out(item) -> TaskSchedulerSettingOut:
    return TaskSchedulerSettingOut(enabled=item.enabled, crontab=item.crontab, timezone=item.timezone)
