"""Pure in-memory preview planning. No database or drive adapter dependencies."""
from __future__ import annotations
import os
import re
import logging
from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from app.application.dto.task_browse import SharePreviewItemOut
from app.application.drive_payload import bool_is_dir, pick_children_count, pick_fid, pick_fid_token, pick_name, pick_size, pick_updated_at
from app.core.errors import bad_request
from app.domain.ports.share_preview import PreviewRenamerPort

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class PreviewPlanOptions:
    taskname: str | None = None
    pattern: str | None = None
    replace: str | None = None
    min_size: str | None = None
    filter_words: str | None = None
    file_filter: str | None = None
    file_filter_mode: str | None = None
    file_min_date: str | None = None
    dir_min_date: str | None = None
    folder_filter: str | None = None
    folder_exclude: str | None = None
    folder_filter_mode: str | None = None
    folder_exclude_mode: str | None = None
    folder_priority: str | None = None
    folder_priority_mode: str | None = None
    max_items: int = 200
    ignore_extension: bool | None = None

def plan_preview_items(payload: PreviewPlanOptions, raw_items: Sequence[Mapping[str, Any]],
                       destination_names: Sequence[str], mr: PreviewRenamerPort,
                       *, min_bytes: int | None = None) -> list[SharePreviewItemOut]:
    taskname = str(payload.taskname or '')
    pattern = str(payload.pattern or '')
    replace = str(payload.replace or '')
    ignore_ext = bool(payload.ignore_extension)
    dir_filename_list = list(destination_names)
    pattern, replace = mr.magic_regex_conv(pattern, replace)
    try:
        compiled_search = re.compile(pattern, re.IGNORECASE) if pattern else None
    except re.error as e:
        raise bad_request("TASK_REGEX_INVALID", f"pattern 正则不合法: {e}")
    def _to_ts(v):
        try:
            return float(v)
        except (TypeError, ValueError, OverflowError):
            return None

    preview_list: list[dict] = []
    for raw in raw_items[: payload.max_items]:
        fid = pick_fid(raw)
        file_name = pick_name(raw)
        if not fid or not file_name:
            continue
        is_dir = bool_is_dir(raw)
        updated_at = pick_updated_at(raw)
        size = pick_size(raw)
        include_items = pick_children_count(raw) if is_dir else None

        item: dict = {
            "fid": fid,
            "fid_token": pick_fid_token(raw),
            "file_name": file_name,
            "dir": is_dir,
            "updated_at": updated_at,
            "size": size,
            "include_items": include_items,
            "file_name_re": None,
            "file_name_saved": None,
        }

        if is_dir:
            # 文件夹筛选/过滤检查
            folder_filter_str = str(payload.folder_filter or "").strip()
            folder_exclude_str = str(payload.folder_exclude or "").strip()
            dir_min_date_str = str(payload.dir_min_date or "").strip()
            folder_priority_str = str(payload.folder_priority or "").strip()
            folder_priority_mode_str = str(payload.folder_priority_mode or "").strip()
            if folder_filter_str or folder_exclude_str or dir_min_date_str or folder_priority_str:
                name_lower = file_name.lower()
                # 优先级匹配检查
                if folder_priority_str:
                    priority_keywords = [w.strip().lower() for w in folder_priority_str.split("|") if w.strip()]
                    priority_is_any = str(folder_priority_mode_str or "all").strip().lower() == "any"
                    if priority_keywords:
                        if priority_is_any:
                            if any(kw in name_lower for kw in priority_keywords):
                                item["priority_match"] = True
                        else:
                            if all(kw in name_lower for kw in priority_keywords):
                                item["priority_match"] = True
                if folder_filter_str:
                    folder_filter_words = [w.strip().lower() for w in folder_filter_str.split("|") if w.strip()]
                    folder_filter_is_any = str(payload.folder_filter_mode or "all").strip().lower() == "any"
                    if folder_filter_words:
                        if folder_filter_is_any:
                            if not any(w in name_lower for w in folder_filter_words):
                                item["filtered_by_folder"] = "不包含筛选词"
                        else:
                            if not all(w in name_lower for w in folder_filter_words):
                                item["filtered_by_folder"] = "不包含筛选词"
                if folder_exclude_str and not item.get("filtered_by_folder"):
                    folder_exclude_words = [w.strip().lower() for w in folder_exclude_str.split("|") if w.strip()]
                    folder_exclude_is_all = str(payload.folder_exclude_mode or "any").strip().lower() == "all"
                    if folder_exclude_words:
                        if folder_exclude_is_all:
                            if all(w in name_lower for w in folder_exclude_words):
                                item["filtered_by_folder"] = "匹配过滤词"
                        else:
                            if any(w in name_lower for w in folder_exclude_words):
                                item["filtered_by_folder"] = "匹配过滤词"
                if dir_min_date_str and not item.get("filtered_by_folder"):
                    dir_ts = updated_at
                    if dir_ts is not None:
                        try:
                            from datetime import datetime as _dt
                            if isinstance(dir_ts, (int, float)) and dir_ts > 0:
                                dir_date = _dt.fromtimestamp(dir_ts / 1000 if dir_ts > 1e12 else dir_ts).strftime("%Y-%m-%d")
                            else:
                                dir_date = str(dir_ts)[:10]
                            if dir_date < dir_min_date_str:
                                item["filtered_by_folder"] = "早于文件夹时间过滤"
                        except (TypeError, ValueError, OverflowError, OSError):
                            logger.warning("Preview directory date could not be interpreted")
            preview_list.append(item)
            continue

        search_re = compiled_search
        matched = (not search_re) or bool(search_re.search(file_name))
        if matched:
            file_name_re = file_name
            if not is_dir:
                file_name_re = mr.sub(pattern, replace, file_name)
            saved = mr.is_exists(file_name_re, dir_filename_list, ignore_ext and not is_dir) if dir_filename_list else None
            if saved:
                item["file_name_saved"] = saved
            else:
                item["file_name_re"] = file_name_re
        else:
            if search_re:
                item["filtered_by_search"] = True
        preview_list.append(item)

    # 最小文件大小过滤标记
    min_size_str = str(payload.min_size or "").strip()
    if min_size_str:
        if min_bytes is not None and min_bytes > 0:
            for f in preview_list:
                if f.get("dir"):
                    continue
                file_size = pick_size(f)
                if file_size is not None and file_size < min_bytes:
                    f["file_name_re"] = None
                    f["file_name_saved"] = None
                    f["filtered_by_size"] = True

    # 关键词过滤标记
    filter_words_str = str(payload.filter_words or "").strip()
    if filter_words_str:
        filter_words = [w.strip().lower() for w in filter_words_str.split("|") if w.strip()]
        if filter_words:
            for f in preview_list:
                if f.get("dir") or f.get("filtered_by_size"):
                    continue
                fname = str(f.get("file_name") or "").lower()
                if any(w in fname for w in filter_words):
                    f["file_name_re"] = None
                    f["file_name_saved"] = None
                    f["filtered_by_keyword"] = True

    # 文件筛选标记
    file_filter_str = str(payload.file_filter or "").strip()
    if file_filter_str:
        file_filter_words = [w.strip().lower() for w in file_filter_str.split("|") if w.strip()]
        if file_filter_words:
            file_filter_is_any = str(payload.file_filter_mode or "all").strip().lower() == "any"
            for f in preview_list:
                if f.get("dir") or f.get("filtered_by_size") or f.get("filtered_by_keyword"):
                    continue
                fname = str(f.get("file_name") or "").lower()
                if file_filter_is_any:
                    matched = any(w in fname for w in file_filter_words)
                else:
                    matched = all(w in fname for w in file_filter_words)
                if not matched:
                    f["file_name_re"] = None
                    f["file_name_saved"] = None
                    f["filtered_by_file_filter"] = True

    # 文件时间过滤标记
    file_min_date_str = str(payload.file_min_date or "").strip()
    if file_min_date_str:
        from datetime import datetime as _dt
        for f in preview_list:
            if f.get("dir") or f.get("filtered_by_size") or f.get("filtered_by_keyword") or f.get("filtered_by_file_filter"):
                continue
            file_ts = f.get("updated_at")
            if file_ts is not None:
                try:
                    if isinstance(file_ts, (int, float)) and file_ts > 0:
                        file_date = _dt.fromtimestamp(file_ts / 1000 if file_ts > 1e12 else file_ts).strftime("%Y-%m-%d")
                    else:
                        file_date = str(file_ts)[:10]
                    if file_date < file_min_date_str:
                        f["file_name_re"] = None
                        f["file_name_saved"] = None
                        f["filtered_by_file_date"] = True
                except (TypeError, ValueError, OverflowError, OSError):
                    logger.warning("Preview file date could not be interpreted")

    # 过滤完成后再判断重命名冲突，避免已被关键词/文件规则排除的文件参与冲突。
    best: dict[str, tuple[tuple[float, float], int]] = {}
    for idx, f in enumerate(preview_list):
        if f.get("file_name_saved") or f.get("dir"):
            continue
        target = f.get("file_name_re")
        if not target:
            continue
        key = os.path.splitext(target)[0] if ignore_ext else target
        sz = pick_size(f)
        ts = _to_ts(f.get("updated_at"))
        score = (float(sz) if sz is not None else float("-inf"), ts if ts is not None else float("-inf"))
        prev = best.get(key)
        if prev is None or score > prev[0] or (score == prev[0] and idx > prev[1]):
            best[key] = (score, idx)
    if best:
        keep_idx = set(v[1] for v in best.values())
        for idx, f in enumerate(preview_list):
            if idx in keep_idx or f.get("file_name_saved") or f.get("dir"):
                continue
            if f.get("file_name_re"):
                f["file_name_saved"] = "重命名冲突（保留最大）"
                f["file_name_re"] = None

    items: list[SharePreviewItemOut] = []
    for it in preview_list:
        items.append(
            SharePreviewItemOut(
                fid=str(it["fid"]),
                fid_token=it.get("fid_token"),
                name=str(it["file_name"]),
                name_re=it.get("file_name_re"),
                is_dir=bool(it.get("dir")),
                updated_at=it.get("updated_at"),
                size=it.get("size"),
                children_count=it.get("include_items") if it.get("dir") else None,
                file_name=str(it["file_name"]),
                file_name_re=it.get("file_name_re"),
                file_name_saved=it.get("file_name_saved"),
                filtered_by_size=bool(it.get("filtered_by_size")) if it.get("filtered_by_size") else None,
                filtered_by_keyword=bool(it.get("filtered_by_keyword")) if it.get("filtered_by_keyword") else None,
                filtered_by_file_filter=bool(it.get("filtered_by_file_filter")) if it.get("filtered_by_file_filter") else None,
                filtered_by_file_date=bool(it.get("filtered_by_file_date")) if it.get("filtered_by_file_date") else None,
                filtered_by_folder=it.get("filtered_by_folder") or None,
                filtered_by_search=bool(it.get("filtered_by_search")) if it.get("filtered_by_search") else None,
                priority_match=bool(it.get("priority_match")) if it.get("priority_match") else None,
                dir=bool(it.get("dir")),
                include_items=it.get("include_items") if it.get("dir") else None,
            )
        )
    return items
