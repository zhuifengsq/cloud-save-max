from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable
from app.application.drama_file_rules import ExistingDramaFiles, conflict_winners, rename_candidate
from app.infrastructure.execution.magic_rename import MagicRename

def _parse_size(size_str: str) -> int | None:
    """将 '100MB' 等大小字符串解析为字节数"""
    if not size_str or not size_str.strip():
        return None
    s = size_str.strip().upper()
    units = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
    for suffix, multiplier in sorted(units.items(), key=lambda x: -len(x[0])):
        if s.endswith(suffix):
            try:
                return int(float(s[: -len(suffix)]) * multiplier)
            except (ValueError, TypeError, OverflowError):
                return None
    try:
        return int(float(s))
    except (ValueError, TypeError, OverflowError):
        return None

def _to_size(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None

def _normalize_name(name: str, ignore_extension: bool) -> str:
    normalized = name.strip().lower()
    if not ignore_extension:
        return normalized
    if "." in normalized:
        return normalized.rsplit(".", 1)[0]
    return normalized

def _is_dir(payload: dict[str, Any]) -> bool:
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

def _get_name(payload: dict[str, Any]) -> str:
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

def _get_fid(payload: dict[str, Any]) -> str:
    return str(payload.get("fid") or payload.get("fs_id") or payload.get("file_id") or payload.get("id") or payload.get("fileId") or "")

def _get_fid_token(payload: dict[str, Any]) -> str | None:
    value = payload.get("fid_token") or payload.get("share_fid_token") or payload.get("token")
    if value is None:
        return None
    return str(value)

def _get_updated_at(payload: dict[str, Any]):
    return payload.get("updated_at") or payload.get("update_time") or payload.get("mtime") or payload.get("modified_at")

@dataclass(slots=True)
class DramaPlanItem:
    fid: str
    fid_token: str | None
    origin_name: str
    target_name: str

def build_transfer_plan(task_data: dict[str, Any], *, filter_files: Callable, share_files: list[dict[str, Any]], dest_file_list: list[dict[str, Any]]) -> list[DramaPlanItem]:
    taskname = str(task_data.get("taskname") or "")
    pattern = str(task_data.get("pattern") or "")
    replace = str(task_data.get("replace") or "")
    ignore_extension = bool(task_data.get("ignore_extension"))

    mr = MagicRename(magic_regex=(task_data.get("magic_regex") if isinstance(task_data.get("magic_regex"), dict) else None))
    mr.set_taskname(taskname)
    pattern, replace = mr.magic_regex_conv(pattern, replace)
    compiled = re.compile(pattern, re.IGNORECASE) if pattern else None
    existing = ExistingDramaFiles(
        [_get_name(raw) for raw in dest_file_list if not _is_dir(raw)],
        ignore_extension=ignore_extension, search=compiled, replace=replace, renamer=mr,
    )

    candidates: list[dict[str, Any]] = []
    for raw in share_files:
        fid = _get_fid(raw)
        origin_name = _get_name(raw)
        fid_token = _get_fid_token(raw)
        updated_at = _get_updated_at(raw)
        if not fid or not origin_name:
            continue
        file_name_re = rename_candidate(origin_name, match=compiled, replace=replace, renamer=mr)
        if not file_name_re or (not _is_dir(raw) and existing.match(origin_name, file_name_re)):
            continue
        candidates.append(
            {
                "fid": fid,
                "fid_token": fid_token,
                "file_name": origin_name,
                "file_name_re": file_name_re,
                "updated_at": updated_at,
                "size": raw.get("size"),
                "dir": False,
            }
        )

    candidates = filter_files(candidates)

    if candidates and replace and re.search(r"\{I+\}", replace):
        normalized_dest = [
            {"file_name": _get_name(raw), "dir": _is_dir(raw)}
            for raw in dest_file_list
            if _get_name(raw)
        ]
        mr.set_dir_file_list(normalized_dest, replace)
        mr.sort_file_list(candidates)

    keep_idx = conflict_winners(
        ((f.get("file_name_re"), _to_size(f.get("size")), f.get("updated_at")) for f in candidates),
        ignore_extension,
    )

    plan: list[DramaPlanItem] = []
    for idx, f in enumerate(candidates):
        if idx not in keep_idx:
            continue
        plan.append(
            DramaPlanItem(
                fid=str(f["fid"]),
                fid_token=(str(f.get("fid_token")) if f.get("fid_token") is not None else None),
                origin_name=str(f["file_name"]),
                target_name=str(f["file_name_re"]),
            )
        )
    return plan
