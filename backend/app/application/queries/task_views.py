"""任务读模型：列表 / 详情 / 执行记录 / 调度设置（application/queries）。

为什么放在这一层：路由原先自己编排「查任务 + 批量取 TMDB 状态 + 批量取 payload +
批量取引流摘要 + 逐条拼装」，改一次展示口径要动路由，且容易漏掉批量化而退化成
N+1。现在口径固定在 `list_task_views()` 一处，路由只剩一行。

契约（与 S5 前逐字节一致，见 regression_tests/test_task_presenter_contract.py）：
- executions 按 started_at 倒序（缺失时间视为最早），由 presenter 截断为 3 条；
- extra/addition/adapter_snapshot 的 JSON 解析与容错口径不变；
- 追剧进度 = TMDB 最新已播 + 任务 extra.saved_latest；引流摘要默认 {"state": "none"}。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.application.lead_in.records import load_lead_in_summary_map
from app.application.media_category import categorize
from app.application.drama_update_progress import build_drama_update_progress
from app.application.drama_schedule import resolve_item_run_schedule, resolve_run_schedule
from app.application.dto.task import DramaRunScheduleOut, DramaUpdateProgressOut
from app.application.tasks import list_recent_executions, list_tasks_recent_executions
from app.application.tmdb_settings import is_tmdb_configured
from app.application.tmdb_view import (
    get_tmdb_status,
    load_tmdb_payload_map,
    load_tmdb_status_map,
    load_tmdb_weekday_map,
    tmdb_cache_key,
)

DEFAULT_EXECUTIONS_PER_TASK = 3
_EPOCH = datetime.min.replace(tzinfo=timezone.utc)
_EMPTY_LEAD_IN: dict[str, Any] = {"state": "none", "last_added_at": None}


def _load_json_dict(raw: Any) -> dict[str, Any]:
    """容错解析 *_json 文本列；解析失败按空字典处理（与改造前行为一致）。"""
    if isinstance(raw, dict):
        return dict(raw)
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class TaskExecutionView:
    id: int
    task_id: int
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    tree_summary: str | None = None
    message: str | None = None
    stage: str | None = None
    run_log: str | None = None
    adapter_snapshot: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskView:
    id: int
    task_uid: str
    task_type: str
    taskname: str
    shareurl: str
    savepath: str
    ignore_extension: bool
    enabled: bool
    created_at: datetime
    updated_at: datetime
    pattern: str | None = None
    replace: str | None = None
    account_name: str | None = None
    tmdb_id: int | None = None
    tmdb_media_type: str | None = None
    tmdb_status: str | None = None
    tmdb_is_ended: bool | None = None
    media_category: str | None = None
    drama_update_progress: DramaUpdateProgressOut | None = None
    lead_in: dict[str, Any] = field(default_factory=lambda: dict(_EMPTY_LEAD_IN))
    addition: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    executions: tuple[TaskExecutionView, ...] = ()
    run_schedule: DramaRunScheduleOut | None = None


@dataclass(frozen=True)
class TaskSchedulerSettingView:
    enabled: bool
    crontab: str
    timezone: str


def execution_view(item: Any) -> TaskExecutionView:
    """TaskExecution ORM 行 → 执行记录视图。"""
    return TaskExecutionView(
        id=int(getattr(item, "id", 0) or 0),
        task_id=int(getattr(item, "task_id", 0) or 0),
        status=str(getattr(item, "status", "") or ""),
        started_at=getattr(item, "started_at", None),
        finished_at=getattr(item, "finished_at", None),
        tree_summary=getattr(item, "tree_summary", None),
        message=getattr(item, "message", None),
        stage=getattr(item, "stage", None),
        run_log=getattr(item, "run_log", None),
        adapter_snapshot=(getattr(item, "adapter_snapshot", None) if isinstance(getattr(item, "adapter_snapshot", None), dict) else _load_json_dict(getattr(item, "adapter_snapshot", None))),
    )


def task_view(
    db: Session | None,
    item: Any,
    *,
    executions: list[Any] | None = None,
    tmdb_status_map: dict[tuple[str, int], str | None] | None = None,
    tmdb_payload_map: dict[tuple[str, int], dict[str, Any] | None] | None = None,
    lead_in_map: dict[int, dict[str, Any]] | None = None,
    run_schedule: DramaRunScheduleOut | None = None,
    tmdb_weekday_map: dict[tuple[str, int], tuple[int, ...]] | None = None,
    tmdb_configured: bool | None = None,
) -> TaskView:
    """单个任务 → 视图。

    executions 显式传入时不碰 relationship；未传时读取已装配集合（模型级 eager
    loading 已移除，不会触发全量懒加载）。tmdb_*_map 由列表场景批量传入，单条
    场景（创建/更新后回显）可省略，此时按需要单独查一次 TMDB 状态。
    """
    raw_executions = list(executions) if executions is not None else list(getattr(item, "executions", None) or [])
    raw_executions.sort(key=lambda x: getattr(x, "started_at", None) or _EPOCH, reverse=True)

    key = tmdb_cache_key(item)
    tmdb_status: str | None = None
    tmdb_is_ended: bool | None = None
    payload: dict[str, Any] | None = None
    if key is not None:
        if isinstance(tmdb_status_map, dict):
            tmdb_status = tmdb_status_map.get(key)
        elif db is not None:
            tmdb_status = get_tmdb_status(db, key[0], key[1])
        if key[0] == "tv" and tmdb_status is not None:
            tmdb_is_ended = tmdb_status in ("Ended", "Canceled")

    extra = _load_json_dict(getattr(item, "extra_json", None))
    drama_update_progress: DramaUpdateProgressOut | None = None
    media_category: str | None = None
    if str(getattr(item, "task_type", "") or "") == "drama":
        if isinstance(tmdb_payload_map, dict) and key is not None:
            payload = tmdb_payload_map.get(key)
        if payload is None and key is not None and db is not None and not (isinstance(tmdb_payload_map, dict) and key in tmdb_payload_map):
            # 单条场景（详情 / 创建后回显）：按需补一次 payload 查询，否则分类会从有值
            # 变成 None，前端筛选条里这个分类也就跟着消失。
            payload = load_tmdb_payload_map(db, [item]).get(key)
        media_category = categorize(getattr(item, "tmdb_media_type", None), payload)
        saved_latest = extra.get("saved_latest") if isinstance(extra, dict) else None
        tmdb_details = payload if (key is not None and key[0] == "tv") else None
        drama_update_progress = build_drama_update_progress(tmdb_details=tmdb_details, saved_latest=saved_latest)

    task_type = str(getattr(item, "task_type", "") or "")
    if run_schedule is not None:
        schedule = run_schedule
    elif task_type == "drama":
        if isinstance(tmdb_weekday_map, dict):
            # 列表场景：更新日由调用方一次批量算好；「TMDB 是否已配置」按需读一次
            # （tmdb_settings 在 Session 级缓存，只有真是 auto 模式的任务才会触发查询）
            days = (tmdb_weekday_map or {}).get(key, ()) if key is not None else ()
            configured = tmdb_configured if tmdb_configured is not None else (is_tmdb_configured(db) if db is not None else False)
            schedule = resolve_run_schedule(
                task_type=task_type,
                extra=extra,
                tmdb_id=getattr(item, "tmdb_id", None),
                tmdb_media_type=getattr(item, "tmdb_media_type", None),
                tmdb_configured=bool(configured),
                episode_weekdays=days,
            ).to_out()
        elif db is not None:
            schedule = resolve_item_run_schedule(db, item, payload=(tmdb_payload_map or {}).get(key) if key else None)
        else:
            schedule = resolve_run_schedule(task_type=task_type, extra=extra).to_out()
    else:
        schedule = resolve_run_schedule(task_type=task_type, extra=extra).to_out()

    lead_in_summary = (lead_in_map or {}).get(int(getattr(item, "id", 0) or 0))
    if not isinstance(lead_in_summary, dict):
        lead_in_summary = dict(_EMPTY_LEAD_IN)

    return TaskView(
        id=int(item.id),
        task_uid=item.task_uid,
        task_type=item.task_type,
        taskname=item.taskname,
        shareurl=item.shareurl,
        savepath=item.savepath,
        ignore_extension=bool(item.ignore_extension),
        enabled=bool(item.enabled),
        created_at=item.created_at,
        updated_at=item.updated_at,
        pattern=item.pattern,
        replace=item.replace,
        account_name=item.account_name,
        tmdb_id=getattr(item, "tmdb_id", None),
        tmdb_media_type=getattr(item, "tmdb_media_type", None),
        tmdb_status=tmdb_status,
        tmdb_is_ended=tmdb_is_ended,
        media_category=media_category,
        drama_update_progress=drama_update_progress,
        lead_in=lead_in_summary,
        addition=_load_json_dict(getattr(item, "addition_json", None)),
        extra=extra,
        executions=tuple(execution_view(x) for x in raw_executions),
        run_schedule=schedule,
    )


def list_task_views(db: Session, *, executions_per_task: int = DEFAULT_EXECUTIONS_PER_TASK) -> list[TaskView]:
    """任务列表视图：固定 1 次任务查询 + 每任务最近 N 条执行记录 + 3 次批量 IN。"""
    items = list(list_tasks_recent_executions(db, limit=executions_per_task))
    if not items:
        return []
    tmdb_status_map = load_tmdb_status_map(db, items)
    tmdb_payload_map = load_tmdb_payload_map(db, items)
    tmdb_weekday_map = load_tmdb_weekday_map(db, items, payload_map=tmdb_payload_map)
    lead_in_map = load_lead_in_summary_map(db, [int(getattr(item, "id", 0) or 0) for item in items])
    return [
        task_view(
            db,
            item,
            tmdb_status_map=tmdb_status_map,
            tmdb_payload_map=tmdb_payload_map,
            lead_in_map=lead_in_map,
            tmdb_weekday_map=tmdb_weekday_map,
        )
        for item in items
    ]


def task_detail_view(db: Session, item: Any, *, executions_limit: int = DEFAULT_EXECUTIONS_PER_TASK) -> TaskView:
    """单个任务详情/回显视图：执行记录与引流摘要各查一次（单任务场景，不存在 N+1）。"""
    task_id = int(getattr(item, "id", 0) or 0)
    executions = list_recent_executions(db, task_id, limit=executions_limit) if executions_limit > 0 else []
    lead_in_map = load_lead_in_summary_map(db, [task_id]) if task_id else {}
    return task_view(db, item, executions=list(executions), lead_in_map=lead_in_map)


def list_recent_execution_views(db: Session, task_id: int, *, limit: int = DEFAULT_EXECUTIONS_PER_TASK) -> list[TaskExecutionView]:
    return [execution_view(item) for item in list_recent_executions(db, int(task_id), limit=limit)]


def scheduler_view(item: Any) -> TaskSchedulerSettingView:
    return TaskSchedulerSettingView(
        enabled=bool(item.enabled),
        crontab=str(item.crontab),
        timezone=str(item.timezone),
    )
