"""Task read orchestration; candidate classification uses O(N) narrow metadata."""

import json
from dataclasses import asdict
from types import SimpleNamespace
from app.application.container import task_reads_repo, execution_reads_repo
from app.application.media_category import categorize, CATEGORY_ORDER
from app.application.queries.task_views import task_view
from app.application.tmdb_view import (
    load_tmdb_status_map,
    load_tmdb_payload_map,
    load_tmdb_weekday_map,
)
from app.application.tmdb_settings import get_tmdb_lang_pair, is_tmdb_configured
from app.application.lead_in.records import load_lead_in_summary_map


def matching_tasks(db, *, media_category=None, failed_only=False, **filters):
    language, poster_language = get_tmdb_lang_pair(db)
    rows = task_reads_repo().candidates(
        db, language=language, poster_language=poster_language, **filters
    )
    result = []
    for row in rows:
        payload = json.loads(row.pop("category_payload") or "{}")
        # A projection with all-null fields represents a missing TMDB cache entry.
        if not any(value is not None for value in payload.values()):
            payload = None
        row["media_category"] = (
            categorize(row["tmdb_media_type"], payload)
            if row["task_type"] == "drama"
            else None
        )
        if media_category and row["media_category"] != media_category:
            continue
        if failed_only and row["latest_status"] != "failed":
            continue
        result.append(row)
    return result


def summaries(db, ids, *, full=False, include_latest_success=False):
    rows = task_reads_repo().read_tasks(db, ids, full=full)
    if not rows:
        return []
    items = [SimpleNamespace(**row) for row in rows]
    statuses = load_tmdb_status_map(db, items)
    payloads = load_tmdb_payload_map(db, items)
    weekdays = load_tmdb_weekday_map(db, items, payload_map=payloads)
    lead = load_lead_in_summary_map(db, ids)
    configured = is_tmdb_configured(db)
    executions = execution_reads_repo().read_recent_summaries(db, ids)
    if include_latest_success:
        successful = execution_reads_repo().read_recent_summaries(
            db, ids, limit=1, status="success"
        )
        for task_id, successful_items in successful.items():
            recent = executions.setdefault(task_id, [])
            if successful_items[0]["id"] not in {item["id"] for item in recent}:
                recent.append(successful_items[0])
    result = {}
    for item in items:
        view = task_view(
            None,
            item,
            executions=[],
            tmdb_status_map=statuses,
            tmdb_payload_map=payloads,
            tmdb_weekday_map=weekdays,
            lead_in_map=lead,
            tmdb_configured=configured,
        )
        output = asdict(view)
        output["executions"] = executions.get(item.id, [])
        output["drive_type"] = item.drive_type
        result[item.id] = output
    return [result[task_id] for task_id in ids if task_id in result]


def page_tasks(db, *, page=1, page_size=20, ids=None, **filters):
    rows = matching_tasks(db, **filters)
    # Navigation choices describe the whole task type, so selecting a choice
    # (or finding no matches) cannot remove the other filter buttons.
    has_filter = (
        filters.get("q")
        or filters.get("enabled") is not None
        or filters.get("drive_type")
        or filters.get("media_category")
        or filters.get("failed_only")
    )
    option_rows = (
        matching_tasks(db, task_type=filters.get("task_type")) if has_filter else rows
    )
    matching = {row["id"] for row in rows}
    if ids is not None:
        if (
            len(ids) > page_size
            or len(ids) != len(set(ids))
            or not set(ids) <= matching
        ):
            raise ValueError(
                "Selected IDs must be unique, match filters, and fit page_size"
            )
        selected = ids
    else:
        selected = [
            row["id"] for row in rows[(page - 1) * page_size : page * page_size]
        ]
    categories = {row["media_category"] for row in option_rows}
    return {
        "items": summaries(db, selected),
        "total": len(rows),
        "page": page,
        "page_size": page_size,
        "facets": {
            "categories": [value for value in CATEGORY_ORDER if value in categories],
            "drives": sorted({row["drive_type"] for row in option_rows if row["drive_type"]}),
            "failed_count": sum(row["latest_status"] == "failed" for row in rows),
        },
    }


def order_index(db, **filters):
    return {
        "items": [
            {key: row[key] for key in ("id", "taskname", "drive_type")}
            for row in matching_tasks(db, **filters)
        ]
    }


def overview_tasks(db):
    rows = matching_tasks(db)
    return summaries(db, [row["id"] for row in rows], include_latest_success=True)


def detail_task(db, task_id):
    rows = summaries(db, [task_id], full=True)
    return rows[0] if rows else None
