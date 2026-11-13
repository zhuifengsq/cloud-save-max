from __future__ import annotations

import re
import os
from dataclasses import dataclass
from typing import Any, Callable
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
    compiled_search = re.compile(pattern, re.IGNORECASE) if pattern else None
    tmdb_series_title = str(task_data.get("tmdb_series_title") or "").strip() or None
    tmdb_tv_seasons = task_data.get("tmdb_tv_seasons") if isinstance(task_data.get("tmdb_tv_seasons"), list) else None
    tmdb_year = int(task_data.get("tmdb_year")) if isinstance(task_data.get("tmdb_year"), int) else None
    tmdb_media_type = str(task_data.get("tmdb_media_type") or "").strip().lower()

    def _to_ts(v):
        try:
            return float(v)
        except Exception:
            return None

    dest_filename_list = []
    dest_episode_set: set[tuple[int, int]] = set()
    from app.application.episode_number import extract_episode
    mr._resolved_pattern = pattern
    mr._resolved_replace = replace
    for raw in dest_file_list:
        if _is_dir(raw):
            continue
        name = _get_name(raw)
        if name:
            dest_filename_list.append(name)
            try:
                s, e = extract_episode(name, mr=mr)
                if s is not None and e is not None:
                    dest_episode_set.add((int(s), int(e)))
            except Exception:
                pass

    candidates: list[dict[str, Any]] = []
    for raw in share_files:
        fid = _get_fid(raw)
        origin_name = _get_name(raw)
        fid_token = _get_fid_token(raw)
        updated_at = _get_updated_at(raw)
        if not fid or not origin_name:
            continue
        if not _is_dir(raw):
            # 基于原始文件名去重：文件名已存在则跳过
            if origin_name in dest_filename_list:
                continue
            # 基于集数去重：重命名后文件名变了但集数相同
            if dest_episode_set:
                try:
                    s, e = extract_episode(origin_name, mr=mr)
                    if s is not None and e is not None and (int(s), int(e)) in dest_episode_set:
                        continue
                except Exception:
                    pass
        file_name_re = mr.sub(pattern, replace, origin_name) if (pattern or replace) else origin_name
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

    # 过滤完成后再判断重命名冲突，避免已排除的文件抢占目标名称。
    best: dict[str, tuple[tuple[float, float], int]] = {}
    for idx, f in enumerate(candidates):
        target = str(f.get("file_name_re") or "")
        if not target:
            continue
        key = os.path.splitext(target)[0] if ignore_extension else target
        sz = _to_size(f.get("size"))
        ts = _to_ts(f.get("updated_at"))
        score = (float(sz) if sz is not None else float("-inf"), ts if ts is not None else float("-inf"))
        prev = best.get(key)
        if prev is None or score > prev[0] or (score == prev[0] and idx > prev[1]):
            best[key] = (score, idx)
    keep_idx = set(v[1] for v in best.values())
    candidates = [f for idx, f in enumerate(candidates) if idx in keep_idx]

    plan: list[DramaPlanItem] = []
    for f in candidates:
        plan.append(
            DramaPlanItem(
                fid=str(f["fid"]),
                fid_token=(str(f.get("fid_token")) if f.get("fid_token") is not None else None),
                origin_name=str(f["file_name"]),
                target_name=str(f["file_name_re"]),
            )
        )
    return plan
