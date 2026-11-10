"""任务视图 → Pydantic 响应模型（纯函数，不碰 Session）。

读什么、怎么批量查都在 application/queries/task_views.py 决定；这里只负责把
视图映射成响应模型，并保留对外契约里的两个硬约束：
- executions 固定最多 3 条（倒序由查询层保证）；
- lead_in 缺省为 {"state": "none", "last_added_at": None}。
"""

from __future__ import annotations

from app.application.dto.task import DramaRunScheduleOut
from app.schemas.task import TaskExecutionOut, TaskOut
from app.schemas.task_scheduler import TaskSchedulerSettingOut
from app.application.queries.task_views import TaskExecutionView, TaskSchedulerSettingView, TaskView

MAX_EXECUTIONS_PER_TASK = 3


def execution_out(view: TaskExecutionView) -> TaskExecutionOut:
    return TaskExecutionOut(
        id=view.id,
        task_id=view.task_id,
        status=view.status,
        started_at=view.started_at,
        finished_at=view.finished_at,
        tree_summary=view.tree_summary,
        message=view.message,
        stage=view.stage,
        run_log=view.run_log,
        adapter_snapshot=view.adapter_snapshot,
    )


def task_out(view: TaskView) -> TaskOut:
    return TaskOut(
        id=view.id,
        task_uid=view.task_uid,
        task_type=view.task_type,
        taskname=view.taskname,
        shareurl=view.shareurl,
        savepath=view.savepath,
        pattern=view.pattern,
        replace=view.replace,
        ignore_extension=view.ignore_extension,
        account_name=view.account_name,
        tmdb_id=view.tmdb_id,
        tmdb_media_type=view.tmdb_media_type,
        tmdb_status=view.tmdb_status,
        tmdb_is_ended=view.tmdb_is_ended,
        drama_update_progress=view.drama_update_progress,
        lead_in=view.lead_in,
        enabled=view.enabled,
        addition=view.addition,
        extra=view.extra,
        executions=[execution_out(x) for x in view.executions[:MAX_EXECUTIONS_PER_TASK]],
        created_at=view.created_at,
        updated_at=view.updated_at,
        run_schedule=view.run_schedule or DramaRunScheduleOut(),
    )


def scheduler_out(view: TaskSchedulerSettingView) -> TaskSchedulerSettingOut:
    return TaskSchedulerSettingOut(enabled=view.enabled, crontab=view.crontab, timezone=view.timezone)
