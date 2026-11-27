from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.application.container import system_settings_repo, task_executions_repo, tasks_repo
from app.application.generated_share_check import FAILURE_STAGE, read_share_check
from app.application.notifications.task_notify import extract_root_new_files
from app.application.shareurl_update_logs import read_task_shareurl_update_logs
from app.application.task_scheduler import get_or_create_task_scheduler_setting

# 「近期失败」清理水位：清理失败记录时写入；早于该时刻的「分享链接检测失败」不再出现在仪表盘。
# 只影响展示，不改写执行记录本身——分享链接的真实状态仍由任务列表的检测徽章如实展示。
DRAMA_FAILURES_CLEARED_KEY = "dashboard.drama_failures_cleared_at"
RECENT_UPDATE_LIMIT = 7


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


def build_drama_overview(db: Session, *, days: int = 30, include_shareurl_logs: bool = False) -> dict:
    try:
        window_days = int(days)
    except (TypeError, ValueError):
        window_days = 30
    window_days = max(1, min(365, window_days))
    
    now_utc = datetime.now()
    since = now_utc - timedelta(days=window_days)
    today_start = datetime(now_utc.year, now_utc.month, now_utc.day)
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
    recent_updates: dict[tuple[str, int], dict] = {}
    tmdb_ids = {task.id: task.tmdb_id for task in tasks if (task.tmdb_id or 0) > 0}
    cleared_at = read_failures_cleared_at(db)

    # 只取仪表盘需要的列：执行记录含 run_log/tree_summary 等大文本字段，
    # 加载整行会让窗口内的数千条记录额外搬运十几 MB 数据（合成库实测 103ms → 36ms）。
    executions = tasks_repo().list_drama_execution_rows(db, since=since)

    for status, started_at, finished_at, task_id, stage, message, snapshot, taskname, legacy_tree in executions:
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

        if status == "success":
            is_today = started_at >= today_start
            drama_key = ("tmdb", tmdb_ids[task_id]) if task_id in tmdb_ids else ("task", task_id)
            if drama_key not in recent_updates and (is_today or len(recent_updates) < RECENT_UPDATE_LIMIT):
                data = _safe_json_object(snapshot)
                count = data.get("transfer_count")
                names = data.get("new_files")
                if not isinstance(names, list):
                    names = extract_root_new_files((legacy_tree or "").partition("\n")[2])
                clean_names = [str(x).strip() for x in names if str(x or "").strip()]
                raw_count = count if type(count) is int and count > 0 else 0
                effective_count = max(raw_count, len(clean_names))
                if clean_names or effective_count > 0:
                    recent_updates[drama_key] = {
                        "task_id": task_id,
                        "started_at": started_at,
                        "drive_type": str(data.get("drive_type") or "") or None,
                        "transfer_count": effective_count,
                        "new_files": clean_names[:2],
                    }

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
        "recent_updates": list(recent_updates.values()),
        "recent_failures": recent_failures,
        "shareurl_update_logs": [
            entry for task in tasks for entry in read_task_shareurl_update_logs(task)
        ] if include_shareurl_logs else [],
        "updated_at": now_utc,
    }


def _clean_task_display_title(raw_title: object, task_id: int) -> str:
    text = str(raw_title or "").strip()
    if not text:
        return f"任务 #{task_id}"
    return text.split(" · ", 1)[0].strip() or text


def build_drama_updates(db: Session, *, range_key: str = "today") -> dict:
    from app.application.container import drive_accounts_repo

    normalized_range = range_key if range_key in {"today", "24h", "7d", "30d"} else "today"
    now = datetime.now()
    if normalized_range == "today":
        since = datetime(now.year, now.month, now.day)
    elif normalized_range == "24h":
        since = now - timedelta(hours=24)
    elif normalized_range == "7d":
        since = now - timedelta(days=7)
    else:
        since = now - timedelta(days=30)

    tasks = tasks_repo().list_drama_tasks(db)
    tasks_by_id = {int(task.id): task for task in tasks}
    account_drive_cache: dict[str, str | None] = {}
    executions = list(tasks_repo().list_drama_execution_rows(db, since=since))
    executions.sort(key=lambda row: row[1], reverse=True)

    groups_by_key: dict[str, dict] = {}
    for status, started_at, _finished_at, task_id, _stage, _message, snapshot, taskname, legacy_tree in executions:
        if str(status or "").strip().lower() != "success":
            continue
        data = _safe_json_object(snapshot)
        count = data.get("transfer_count")
        names = data.get("new_files")
        if not isinstance(names, list):
            names = extract_root_new_files((legacy_tree or "").partition("\n")[2])
        clean_names = [str(x).strip() for x in names if str(x or "").strip()]
        raw_count = count if type(count) is int and count > 0 else 0
        effective_count = max(raw_count, len(clean_names))
        if not clean_names and effective_count <= 0:
            continue

        task_id_int = int(task_id)
        task = tasks_by_id.get(task_id_int)
        tmdb_id_val = getattr(task, "tmdb_id", None) if task is not None else None
        tmdb_id = int(tmdb_id_val) if tmdb_id_val is not None and int(tmdb_id_val) > 0 else None
        title = _clean_task_display_title(getattr(task, "taskname", None) or taskname, task_id_int)
        if tmdb_id is not None:
            drama_key = f"tmdb:{tmdb_id}"
        elif title:
            drama_key = f"name:{title.lower()}"
        else:
            drama_key = f"task:{task_id_int}"

        drive_type = str(data.get("drive_type") or "").strip() or None
        account_name = str(getattr(task, "account_name", "") or "").strip() or None
        if not drive_type and account_name:
            if account_name not in account_drive_cache:
                resolved = drive_accounts_repo().get_drive_type_by_name(db, name=account_name)
                account_drive_cache[account_name] = str(resolved or "").strip() or None
            drive_type = account_drive_cache[account_name]

        group = groups_by_key.get(drama_key)
        if group is None:
            group = {
                "drama_key": drama_key,
                "tmdb_id": tmdb_id,
                "title": title,
                "latest_at": started_at,
                "total_transfer_count": 0,
                "_drives": {},
            }
            groups_by_key[drama_key] = group
        elif started_at > group["latest_at"]:
            group["latest_at"] = started_at

        drive_entry = group["_drives"].get(task_id_int)
        if drive_entry is None:
            drive_entry = {
                "task_id": task_id_int,
                "drive_type": drive_type,
                "account_name": account_name,
                "started_at": started_at,
                "transfer_count": 0,
                "new_files": [],
                "_seen_files": set(),
                "execution_count": 0,
            }
            group["_drives"][task_id_int] = drive_entry
        else:
            if started_at > drive_entry["started_at"]:
                drive_entry["started_at"] = started_at
            if not drive_entry["drive_type"] and drive_type:
                drive_entry["drive_type"] = drive_type

        drive_entry["transfer_count"] += effective_count
        drive_entry["execution_count"] += 1
        group["total_transfer_count"] += effective_count
        for fname in clean_names:
            if len(drive_entry["new_files"]) >= 6:
                break
            if fname not in drive_entry["_seen_files"]:
                drive_entry["_seen_files"].add(fname)
                drive_entry["new_files"].append(fname)

    groups: list[dict] = []
    drive_update_count = 0
    total_transfer_count = 0
    for group in sorted(groups_by_key.values(), key=lambda g: g["latest_at"], reverse=True):
        drives = []
        for entry in sorted(group["_drives"].values(), key=lambda d: d["started_at"], reverse=True):
            entry.pop("_seen_files", None)
            drives.append(entry)
        drive_update_count += len(drives)
        total_transfer_count += int(group["total_transfer_count"])
        groups.append(
            {
                "drama_key": group["drama_key"],
                "tmdb_id": group["tmdb_id"],
                "title": group["title"],
                "latest_at": group["latest_at"],
                "total_transfer_count": group["total_transfer_count"],
                "drives": drives,
            }
        )

    return {
        "range": normalized_range,
        "since": since,
        "drama_count": len(groups),
        "drive_update_count": drive_update_count,
        "total_transfer_count": total_transfer_count,
        "groups": groups,
    }
