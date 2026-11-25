"""Pure in-memory preview planning. No database or drive adapter dependencies."""
from __future__ import annotations
import re
import logging
from dataclasses import dataclass
from typing import Mapping, Sequence, TypedDict, NotRequired
from app.application.dto.task_browse import SharePreviewItemOut
from app.application.drive_payload import bool_is_dir, pick_children_count, pick_fid, pick_fid_token, pick_name, pick_size, pick_updated_at
from app.application.drama_file_rules import ExistingDramaFiles, FileFilters, conflict_winners, rename_candidate, split_words
from app.application.episode_number import parse_size
from app.core.errors import bad_request
from app.domain.ports.share_preview import PreviewRenamerPort

logger = logging.getLogger(__name__)

class PreviewRow(TypedDict):
    fid: str
    fid_token: str | None
    file_name: str
    dir: bool
    updated_at: object
    size: int | None
    include_items: int | None
    file_name_re: str | None
    file_name_saved: str | None
    priority_match: NotRequired[bool]
    filtered_by_folder: NotRequired[str]
    filtered_by_search: NotRequired[bool]
    filtered_by_size: NotRequired[bool]
    filtered_by_keyword: NotRequired[bool]
    filtered_by_file_filter: NotRequired[bool]
    filtered_by_file_date: NotRequired[bool]

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

def plan_preview_items(payload: PreviewPlanOptions, raw_items: Sequence[Mapping[str, object]],
                       destination_names: Sequence[str], mr: PreviewRenamerPort,
                       *, min_bytes: int | None = None) -> list[SharePreviewItemOut]:
    pattern = str(payload.pattern or '')
    replace = str(payload.replace or '')
    ignore_ext = bool(payload.ignore_extension)
    pattern, replace = mr.magic_regex_conv(pattern, replace)
    try:
        compiled_search = re.compile(pattern, re.IGNORECASE) if pattern else None
    except re.error as e:
        raise bad_request("TASK_REGEX_INVALID", f"pattern 正则不合法: {e}")
    preview_list = _collect_rows(payload, raw_items, destination_names, mr, pattern, replace, compiled_search)
    _apply_filters(preview_list, payload, min_bytes)
    _mark_conflicts(preview_list, ignore_ext)
    return _present_rows(preview_list)

def _mark_folder(item: PreviewRow, payload: PreviewPlanOptions) -> None:
    file_name = item["file_name"]
    updated_at = item["updated_at"]
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

def _collect_rows(payload: PreviewPlanOptions, raw_items: Sequence[Mapping[str, object]],
                  destination_names: Sequence[str], mr: PreviewRenamerPort, pattern: str, replace: str,
                  compiled_search: re.Pattern[str] | None) -> list[PreviewRow]:
    ignore_ext = bool(payload.ignore_extension)
    existing = ExistingDramaFiles(
        destination_names, ignore_extension=ignore_ext, search=compiled_search, replace=replace, renamer=mr,
    )
    preview_list: list[PreviewRow] = []
    for raw in raw_items[: payload.max_items]:
        fid = pick_fid(raw)
        file_name = pick_name(raw)
        if not fid or not file_name:
            continue
        is_dir = bool_is_dir(raw)
        updated_at = pick_updated_at(raw)
        size = pick_size(raw)
        include_items = pick_children_count(raw) if is_dir else None

        item: PreviewRow = {
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
            _mark_folder(item, payload)
            preview_list.append(item)
            continue

        target = rename_candidate(file_name, match=compiled_search, replace=replace, renamer=mr)
        if target is None:
            item["filtered_by_search"] = True
        elif saved := existing.match(file_name, target):
            item["file_name_saved"] = saved
        else:
            item["file_name_re"] = target
        preview_list.append(item)

    return preview_list

def _apply_filters(preview_list: list[PreviewRow], payload: PreviewPlanOptions, min_bytes: int | None) -> None:
    rules = FileFilters(
        min_bytes=parse_size(payload.min_size or "") if min_bytes is None else min_bytes,
        blocked=split_words(payload.filter_words),
        required=split_words(payload.file_filter),
        match_any=str(payload.file_filter_mode or "").strip().lower() == "any",
        min_date=str(payload.file_min_date or "").strip(),
    )
    for item in preview_list:
        if item["dir"]:
            continue
        reason = rules.rejection(item["file_name"], item["size"], item["updated_at"])
        if reason is None:
            continue
        item["file_name_re"] = None
        item["file_name_saved"] = None
        if reason == "size":
            item["filtered_by_size"] = True
        elif reason == "keyword":
            item["filtered_by_keyword"] = True
        elif reason == "file_filter":
            item["filtered_by_file_filter"] = True
        elif reason == "file_date":
            item["filtered_by_file_date"] = True


def _mark_conflicts(preview_list: list[PreviewRow], ignore_ext: bool) -> None:
    keep = conflict_winners(
        ((item["file_name_re"], item["size"], item["updated_at"]) for item in preview_list),
        ignore_ext,
    )
    for idx, item in enumerate(preview_list):
        if item["file_name_re"] and idx not in keep:
            item["file_name_saved"] = "重命名冲突（保留最大）"
            item["file_name_re"] = None


def _present_rows(preview_list: list[PreviewRow]) -> list[SharePreviewItemOut]:
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
