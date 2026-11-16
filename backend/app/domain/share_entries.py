"""Normalize heterogeneous drive entry JSON without IO."""

import os
from typing import Any

video_exts = {
    ".mp4",
    ".mkv",
    ".avi",
    ".mov",
    ".wmv",
    ".flv",
    ".webm",
    ".m4v",
    ".ts",
    ".m2ts",
    ".mpg",
    ".mpeg",
    ".3gp",
    ".cas",
}


def _bool_is_dir(payload: dict[str, Any]) -> bool:
    if payload.get("is_dir") is not None:
        return bool(payload.get("is_dir"))
    if payload.get("isdir") is not None:
        return str(payload.get("isdir")) in ("1", "true", "True")
    if payload.get("dir") is not None:
        return bool(payload.get("dir"))
    if payload.get("kind") in ("folder", "dir", "directory"):
        return True
    if payload.get("kind") in ("file",):
        return False
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
    return str(
        payload.get("fid")
        or payload.get("fs_id")
        or payload.get("file_id")
        or payload.get("id")
        or payload.get("fileId")
        or ""
    )


def _pick_updated_at(payload: dict[str, Any]) -> Any:
    return (
        payload.get("updated_at")
        or payload.get("update_time")
        or payload.get("mtime")
        or payload.get("modified_at")
    )


def _pick_size(payload: dict[str, Any]) -> int | None:
    value = payload.get("size")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_video_name(name: str) -> bool:
    try:
        _base, _ext = os.path.splitext(str(name or ""))
    except Exception:
        return False
    return bool(_ext) and _ext.lower() in video_exts


def _to_ts(v: Any) -> float | None:
    try:
        return float(v)
    except Exception:
        return None
