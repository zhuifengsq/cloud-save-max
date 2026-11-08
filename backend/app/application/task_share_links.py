from __future__ import annotations

import json
from datetime import datetime
from typing import Any

SHARE_LINK_KEY = "share_link"


def _load_extra(task: Any) -> dict[str, Any]:
    raw = getattr(task, "extra_json", None)
    if not raw:
        return {}
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _store_extra(task: Any, extra: dict[str, Any]) -> None:
    task.extra_json = json.dumps(extra, ensure_ascii=False)


def get_task_share_link(task: Any) -> dict[str, Any] | None:
    """Return the share link saved on the task, if any."""
    record = _load_extra(task).get(SHARE_LINK_KEY)
    if not isinstance(record, dict):
        return None
    share_url = str(record.get("share_url") or "").strip()
    if not share_url:
        return None
    return record


def save_task_share_link(
    task: Any,
    *,
    share_url: str,
    share_id: str = "",
    title: str = "",
    drive_type: str = "",
    account_name: str = "",
    passcode: str | None = None,
    expires_in_days: int | None = None,
) -> dict[str, Any]:
    """Persist the generated share link on the task so it survives reloads."""
    extra = _load_extra(task)
    record = {
        "share_url": str(share_url or "").strip(),
        "share_id": str(share_id or ""),
        "title": str(title or ""),
        "drive_type": str(drive_type or ""),
        "account_name": str(account_name or ""),
        "passcode": passcode or None,
        "expires_in_days": expires_in_days,
        "saved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    extra[SHARE_LINK_KEY] = record
    _store_extra(task, extra)
    return record


def drop_task_share_link(task: Any) -> bool:
    """Remove the saved share link (it no longer matches the task)."""
    extra = _load_extra(task)
    if SHARE_LINK_KEY not in extra:
        return False
    extra.pop(SHARE_LINK_KEY, None)
    _store_extra(task, extra)
    return True


def merge_task_extra(task: Any, extra_payload: dict[str, Any]) -> None:
    """Merge incoming extra config without losing the saved share link."""
    existing = _load_extra(task)
    preserved = existing.get(SHARE_LINK_KEY)
    merged = {**existing, **extra_payload}
    if SHARE_LINK_KEY not in extra_payload and preserved is not None:
        merged[SHARE_LINK_KEY] = preserved
    _store_extra(task, merged)
