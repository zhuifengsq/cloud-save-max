from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.application.container import system_settings_repo, task_executions_repo, tasks_repo
from app.application.generated_share_check import FAILURE_STAGE, read_share_check
from app.application.task_scheduler import get_or_create_task_scheduler_setting

# 「近期失败」清理水位：清理失败记录时写入；早于该时刻的「分享链接检测失败」不再出现在仪表盘。
# 只影响展示，不改写执行记录本身——分享链接的真实状态仍由任务列表的检测徽章如实展示。
DRAMA_FAILURES_CLEARED_KEY = "dashboard.drama_failures_cleared_at"


def _safe_json_object(value: str | None) -> dict:
    if not value:
        return {}
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _parse_naive_datetime(value: object) -> datetime | None:
    """解析 ISO 时间并归一到「本地朴素时间」，与 `datetime.now()` 同一口径。

    历史数据里 `checked_at` 是带时区的本地 ISO（`datetime.now().astimezone()`），
    数据库里的 `started_at` 是朴素本地时间；统一成朴素本地时间后才能和清理水位比较。
    """
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.strip())
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def read_failures_cleared_at(db: Session) -> datetime | None:
    """读取「近期失败」清理水位（没有清理过则为 None）。"""
    return _parse_naive_datetime(system_settings_repo().get_value(db, DRAMA_FAILURES_CLEARED_KEY, ""))


def mark_failures_cleared(db: Session, cutoff: datetime) -> None:
    """推进清理水位；水位只增不减，避免「分批清理」把已清理的记录又放出来。"""
    current = read_failures_cleared_at(db)
    if current is not None and cutoff <= current:
        return
    system_settings_repo().set_value(
        db,
        DRAMA_FAILURES_CLEARED_KEY,
        cutoff.isoformat(timespec="seconds"),
        "仪表盘「近期失败」清理水位：早于该时刻的分享链接检测失败不再展示",
    )


def clear_drama_failures(db: Session, *, days: int = 0, now: datetime | None = None) -> int:
    """清理追剧任务的失败记录。

    1) 删除失败执行记录（原有行为，days>0 时只删该天数之前的）；
    2) 把「分享链接检测失败」的清理水位推进到本次截止时间——这些失败信息挂在**成功**执行记录的
       `adapter_snapshot` 里，删不掉，只能靠水位让仪表盘不再展示，否则「清理完还显示失败任务」。
    """
    try:
        window = int(days)
    except (TypeError, ValueError):
        window = 0
    window = max(0, window)
    current = now or datetime.now()
    cutoff = current - timedelta(days=window) if window > 0 else current
    deleted = int(task_executions_repo().delete_failed_drama_executions(db, days=window, before=cutoff) or 0)
    mark_failures_cleared(db, cutoff)
    return deleted


def _normalize_weekdays(value: object) -> list[int]:
    arr = value if isinstance(value, list) else []
    days: list[int] = []
    for item in arr:
        try:
            n = int(item)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= 7:
            days.append(n)
    return sorted(set(days))


def _daterange(start: date, end: date) -> list[date]:
    if start > end:
        return []
    days: list[date] = []
    cursor = start
    while cursor <= end:
        days.append(cursor)
        cursor = cursor + timedelta(days=1)
    return days


def build_drama_overview(db: Session, *, days: int = 30) -> dict:
    try:
        window_days = int(days)
    except (TypeError, ValueError):
        window_days = 30
    window_days = max(1, min(365, window_days))
    
    now_utc = datetime.now()
    since = now_utc - timedelta(days=window_days)
    month_start = datetime(now_utc.year, now_utc.month, 1)

    scheduler = get_or_create_task_scheduler_setting(db)
    tasks = tasks_repo().list_drama_tasks(db)

    enabled_task_count = 0
    tmdb_bound_count = 0
    unknown_schedule_count = 0

    for task in tasks:
        if task.enabled:
            enabled_task_count += 1
        if task.tmdb_id is not None:
            tmdb_bound_count += 1

        if task.enabled and task.tmdb_id is None:
            extra = _safe_json_object(getattr(task, "extra_json", None))
            runweek = _normalize_weekdays(extra.get("runweek"))
            if not runweek:
                unknown_schedule_count += 1

    execution_total = 0
    execution_success = 0
    execution_failed = 0
    execution_skipped = 0
    duration_sum = 0.0
    duration_count = 0
    monthly_success_count = 0

    start_day = since.date()
    end_day = now_utc.date()
        
    trend_map: dict[str, dict[str, object]] = {
        d.isoformat(): {"total": 0, "success": 0, "failed": 0, "skipped": 0, "dur_sum": 0.0, "dur_cnt": 0}
        for d in _daterange(start_day, end_day)
    }

    recent_failures: list[dict] = []
    cleared_at = read_failures_cleared_at(db)

    # 只取仪表盘需要的列：执行记录含 run_log/tree_summary 等大文本字段，
    # 加载整行会让窗口内的数千条记录额外搬运十几 MB 数据（合成库实测 103ms → 36ms）。
    executions = tasks_repo().list_drama_execution_rows(db, since=since)

    for status, started_at, finished_at, task_id, stage, message, snapshot, taskname in executions:
        status = str(status or "").strip().lower()
        execution_total += 1
        if status == "success":
            execution_success += 1
        elif status == "skipped":
            execution_skipped += 1
        else:
            execution_failed += 1

        day_key = started_at.date().isoformat()
        bucket = trend_map.get(day_key)
        if bucket is not None:
            bucket["total"] = int(bucket["total"]) + 1
            if status == "success":
                bucket["success"] = int(bucket["success"]) + 1
            elif status == "skipped":
                bucket["skipped"] = int(bucket["skipped"]) + 1
            else:
                bucket["failed"] = int(bucket["failed"]) + 1

        if finished_at is not None:
            delta = (finished_at - started_at).total_seconds()
            if delta >= 0 and delta < 3600 * 24 * 2:
                duration_sum += float(delta)
                duration_count += 1
                if bucket is not None:
                    bucket["dur_sum"] = float(bucket["dur_sum"]) + float(delta)
                    bucket["dur_cnt"] = int(bucket["dur_cnt"]) + 1

        if status == "success" and started_at >= month_start:
            monthly_success_count += 1

        if len(recent_failures) < 12 and status == "failed":
            recent_failures.append(
                {
                    "task_id": int(task_id),
                    "taskname": str(taskname or "").strip() or f"任务 #{task_id}",
                    "status": status,
                    "started_at": started_at,
                    "stage": str(stage or "").strip() or None,
                    "message": str(message or "").strip() or None,
                }
            )
        if len(recent_failures) < 12:
            check = read_share_check(snapshot)
            if check.get("status") in {"invalid", "error"}:
                checked_at = check.get("checked_at") or started_at
                checked_dt = _parse_naive_datetime(checked_at)
                # 已被「清理失败记录」清理过的检测失败不再展示（解析不出时间的仍照常展示，宁可多报不漏报）
                if cleared_at is None or checked_dt is None or checked_dt > cleared_at:
                    recent_failures.append({
                        "task_id": int(task_id),
                        "taskname": str(taskname or "").strip() or f"任务 #{task_id}",
                        "status": "failed",
                        "started_at": checked_at,
                        "stage": FAILURE_STAGE,
                        "message": check.get("message"),
                    })

    trend: list[dict] = []
    for d in _daterange(start_day, end_day):
        key = d.isoformat()
        bucket = trend_map.get(key) or {}
        dur_cnt = int(bucket.get("dur_cnt") or 0)
        dur_sum = float(bucket.get("dur_sum") or 0.0)
        trend.append(
            {
                "date": key,
                "total": int(bucket.get("total") or 0),
                "success": int(bucket.get("success") or 0),
                "failed": int(bucket.get("failed") or 0),
                "skipped": int(bucket.get("skipped") or 0),
                "avg_duration_s": round(dur_sum / dur_cnt, 2) if dur_cnt > 0 else None,
            }
        )

    effective_total = execution_success + execution_failed
    success_rate = round(execution_success / effective_total, 4) if effective_total > 0 else None
    avg_duration_s = round(duration_sum / duration_count, 2) if duration_count > 0 else None

    return {
        "scheduler": {
            "enabled": bool(scheduler.enabled),
            "crontab": str(scheduler.crontab),
            "timezone": str(scheduler.timezone),
        },
        "summary": {
            "task_count": len(tasks),
            "enabled_task_count": enabled_task_count,
            "tmdb_bound_count": tmdb_bound_count,
            "unknown_schedule_count": unknown_schedule_count,
            "monthly_success_count": monthly_success_count,
            "window_days": window_days,
            "execution_total": execution_total,
            "execution_success": execution_success,
            "execution_failed": execution_failed,
            "execution_skipped": execution_skipped,
            "success_rate": success_rate,
            "avg_duration_s": avg_duration_s,
        },
        "trend": trend,
        "recent_failures": recent_failures,
        "updated_at": now_utc,
    }
