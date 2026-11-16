from __future__ import annotations


import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.orm import Session

from app.application.container import adapter_factory as _acc_AdapterFactory; AdapterFactory = _acc_AdapterFactory()
from app.application.container import account_manager_cls as _acc_DatabaseAccountManager; DatabaseAccountManager = _acc_DatabaseAccountManager()
from app.application.container import adapter_registry as _acc_AdapterRegistry; AdapterRegistry = _acc_AdapterRegistry()
from app.application.container import task_executions_repo
from app.application.drive_accounts import (
    get_active_account,
    pick_any_default_account,
    pick_default_account_name,
)


def _loads(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        payload = json.loads(value)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _dump_dt(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _loads_json_detail(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        payload = json.loads(value)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _pick_default_account_name(db: Session, drive_type: str) -> str | None:
    return pick_default_account_name(db, drive_type)


def _pick_any_default_account(db: Session) -> Any | None:
    return pick_any_default_account(db)


def _get_active_account(db: Session, account_name: str) -> Any | None:
    return get_active_account(db, account_name)


def _bool_is_dir(payload: dict[str, Any]) -> bool:
    if payload.get("is_dir") is not None:
        return bool(payload.get("is_dir"))
    if payload.get("isdir") is not None:
        return str(payload.get("isdir")) in ("1", "true", "True")
    if payload.get("dir") is not None:
        return bool(payload.get("dir"))
    if payload.get("kind") in ("folder", "dir", "directory"):
        return True
    if payload.get("type") in ("folder", "dir"):
        return True
    if payload.get("type") in ("file",):
        return False
    if payload.get("file_type") is not None:
        value = str(payload.get("file_type"))
        if value in ("0", "dir", "folder"):
            return True
        if value in ("1", "file"):
            return False
    return False


def _pick_name(payload: dict[str, Any]) -> str:
    return str(
        payload.get("file_name")
        or payload.get("server_filename")
        or payload.get("fileName")
        or payload.get("name")
        or payload.get("title")
        or payload.get("fid")
        or payload.get("fs_id")
        or ""
    )


def _pick_fid(payload: dict[str, Any]) -> str:
    return str(payload.get("fid") or payload.get("fs_id") or payload.get("file_id") or payload.get("id") or payload.get("fileId") or "")


def _pick_updated_at(payload: dict[str, Any]) -> Any | None:
    return payload.get("updated_at") or payload.get("modified_at") or payload.get("mtime") or payload.get("update_time")


def _pick_size(payload: dict[str, Any]) -> int | None:
    value = payload.get("size")
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _tmdb_display_title(item: dict[str, Any]) -> str:
    media_type = str(item.get("media_type") or "").strip().lower()
    if media_type == "movie":
        return str(item.get("title") or item.get("original_title") or "").strip()
    return str(item.get("name") or item.get("original_name") or "").strip()


def _tmdb_display_date(item: dict[str, Any]) -> str:
    media_type = str(item.get("media_type") or "").strip().lower()
    if media_type == "movie":
        return str(item.get("release_date") or "").strip()
    return str(item.get("first_air_date") or "").strip()


def _tmdb_year(item: dict[str, Any]) -> str:
    raw = _tmdb_display_date(item)
    return raw[:4] if len(raw) >= 4 else ""


def _tmdb_standard_keyword(item: dict[str, Any]) -> str:
    title = _tmdb_display_title(item)
    year = _tmdb_year(item)
    media_type = str(item.get("media_type") or "").strip().lower()
    if media_type == "movie" and title and year:
        return f"{title} {year}"
    return title


def _first_http_url(text: str) -> str | None:
    normalized = str(text or "").strip().replace("？", "?").replace("＆", "&")
    match = re.search(r"https?://[^\s]+", normalized)
    if not match:
        return None
    return str(match.group(0) or "").strip() or None


def _all_http_urls(text: str) -> list[str]:
    normalized = str(text or "").strip().replace("？", "?").replace("＆", "&")
    urls = [str(x or "").strip() for x in re.findall(r"https?://[^\s]+", normalized)]
    return list(dict.fromkeys([x for x in urls if x]))


def _extract_share_fid(shareurl: str) -> str | None:
    url = str(shareurl or "").strip()
    if not url:
        return None
    match_query = re.search(r"(?:\?|&)fid=([^&#]+)", url)
    if match_query:
        fid = str(match_query.group(1) or "").strip()
        if fid and fid not in ("0", "root"):
            return fid
    match_hash = re.search(r"#/list/share/([a-zA-Z0-9]{6,64})", url)
    if match_hash:
        return str(match_hash.group(1) or "").strip() or None
    match_tail = re.search(r"/([a-fA-F0-9]{32})-?[^/]*$", url)
    if match_tail:
        return str(match_tail.group(1) or "").strip() or None
    return None


def _rewrite_shareurl_with_fid(shareurl: str, fid: str | None) -> str:
    raw = str(shareurl or "").strip()
    target_fid = str(fid or "").strip()
    if not raw:
        return raw
    if "yun.139.com" in raw or "caiyun.139.com" in raw:
        parsed = urlsplit(raw)
        if parsed.fragment:
            frag_path, frag_query = (parsed.fragment.split("?", 1) + [""])[:2]
            frag_pairs = [(k, v) for k, v in parse_qsl(frag_query, keep_blank_values=True) if str(k).lower() != "fid"]
            if target_fid and target_fid not in ("0", "root"):
                frag_pairs.append(("fid", target_fid))
            rebuilt_fragment = frag_path if not frag_pairs else f"{frag_path}?{urlencode(frag_pairs)}"
            return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, rebuilt_fragment)).strip()
        query_pairs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if str(k).lower() != "fid"]
        if target_fid and target_fid not in ("0", "root"):
            query_pairs.append(("fid", target_fid))
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query_pairs), parsed.fragment)).strip()
    if not target_fid or target_fid == "0":
        match = re.search(r".*s/[a-zA-Z0-9\-_]+(\?[^#]*)?", raw)
        return str(match.group(0) if match else raw.split("#")[0]).strip()
    if target_fid in raw:
        match = re.search(rf".*/{re.escape(target_fid)}[^/]*", raw)
        if match:
            return str(match.group(0) or "").strip() or raw
    return f"{raw.split('#')[0]}#/list/share/{target_fid}"


def _is_video_filename(name: str) -> bool:
    return bool(re.search(r"\.(mp4|mkv|mov|m4v|avi|mpeg|ts|flv|wmv|webm|cas)$", str(name or "").strip(), flags=re.IGNORECASE))


def _to_sort_ts(value: Any) -> float:
    try:
        number = float(value)
        return number if number > 1e12 else number * 1000
    except Exception:
        pass
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.timestamp() * 1000
    except Exception:
        return 0.0


def _format_display_datetime(value: Any) -> str:
    if value in (None, ""):
        return "-"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    raw = str(value).strip()
    if not raw:
        return "-"
    match = re.search(r"(-?\d{10,19})", raw)
    if match:
        try:
            number = float(match.group(1))
            while number > 1e12:
                number /= 1000.0
            if number > 0:
                return datetime.fromtimestamp(number).strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass
    try:
        number = float(raw)
        while number > 1e12:
            number /= 1000.0
        if number > 0:
            return datetime.fromtimestamp(number).strftime("%Y-%m-%d %H:%M")
    except Exception:
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return raw


def _tv_progress_text(detail: dict[str, Any]) -> str:
    if not isinstance(detail, dict) or not detail:
        return ""
    seasons = int(detail.get("number_of_seasons") or 0) or 0
    total = int(detail.get("number_of_episodes") or 0) or 0
    last = detail.get("last_episode_to_air") if isinstance(detail.get("last_episode_to_air"), dict) else {}
    last_season = int((last or {}).get("season_number") or 0) or 0
    last_episode = int((last or {}).get("episode_number") or 0) or 0
    parts: list[str] = []
    if seasons > 0:
        parts.append(f"季数:{seasons}")
    if total > 0:
        parts.append(f"总集:{total}")
    if last_season > 0 and last_episode > 0:
        parts.append(f"当前:S{last_season:02d}E{last_episode:02d}")
    return " · ".join(parts)


def _task_execution_summary(execution: Any | None) -> dict[str, Any] | None:
    if execution is None:
        return None
    return {
        "id": int(execution.id),
        "status": str(execution.status or ""),
        "stage": str(getattr(execution, "stage", "") or "") or None,
        "message": str(execution.message or "") or None,
        "started_at": _dump_dt(execution.started_at),
        "finished_at": _dump_dt(execution.finished_at),
    }


def _task_to_dict(db: Session, task: Any) -> dict[str, Any]:
    latest_execution = None
    if "executions" in task.__dict__:
        # 集合已被显式装配（列表场景），直接用，空列表就代表没有执行记录。
        loaded = task.__dict__.get("executions") or []
        if loaded:
            latest_execution = sorted(loaded, key=lambda x: x.id, reverse=True)[0]
    else:
        # 未装配时只查最近一条：绝不能 getattr(task, "executions") 触发全量懒加载。
        latest_execution = task_executions_repo().get_latest_for_task(db, int(task.id))
    return {
        "id": int(task.id),
        "task_uid": str(task.task_uid),
        "task_type": str(task.task_type or ""),
        "taskname": str(task.taskname or ""),
        "shareurl": str(task.shareurl or ""),
        "savepath": str(task.savepath or ""),
        "pattern": task.pattern,
        "replace": task.replace,
        "ignore_extension": bool(task.ignore_extension),
        "account_name": task.account_name,
        "tmdb_id": task.tmdb_id,
        "tmdb_media_type": task.tmdb_media_type,
        "enabled": bool(task.enabled),
        "addition": _loads(task.addition_json),
        "extra": _loads(task.extra_json),
        "latest_execution": _task_execution_summary(latest_execution),
        "created_at": _dump_dt(task.created_at),
        "updated_at": _dump_dt(task.updated_at),
    }


