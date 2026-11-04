"""分享链接预览 / 验证相关端点（从 app.api.routes.tasks 拆分，路径与响应不变）。"""

from __future__ import annotations

import json
import os
import re

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.deps import require_permissions
from app.core.errors import bad_request, not_found
from app.core.permissions import TASK_READ
from app.db.session import get_db
from app.extensions.adapters.adapter_factory import AdapterFactory
from app.extensions.runtime.adapter_registry import AdapterRegistry
from app.extensions.runtime.account_manager import DatabaseAccountManager
from app.extensions.runtime.magic_rename import MagicRename
from app.schemas.task_browse import (
    SharePreviewBatchIn,
    SharePreviewBatchOut,
    SharePreviewIn,
    SharePreviewItemOut,
    SharePreviewOut,
    ShareValidateIn,
    ShareValidateOut,
)
from app.services.share_preview_batch import preview_share_batch, validate_share_links
from app.services.tmdb_settings import get_or_create_tmdb_setting, get_tmdb_runtime_config
from app.services.drive_accounts import get_active_account, pick_default_account_name
from app.services.drive_payload import (
    bool_is_dir,
    pick_children_count,
    pick_fid,
    pick_fid_token,
    pick_name,
    pick_size,
    pick_updated_at,
)

router = APIRouter()


@router.post('/share/preview', response_model=SharePreviewOut, dependencies=[Depends(require_permissions(TASK_READ))])
def post_share_preview(payload: SharePreviewIn, db: Session = Depends(get_db)):
    drive_type = AdapterRegistry.detect_drive_type(payload.shareurl)
    if drive_type is None:
        raise bad_request('TASK_SHAREURL_INVALID', '无法识别的网盘分享链接')
    account_name = payload.account_name or pick_default_account_name(db, drive_type)
    if payload.account_name and get_active_account(db, payload.account_name) is None:
        raise not_found('DRIVE_ACCOUNT_NOT_FOUND', '指定账号不存在或不可用')
    manager = DatabaseAccountManager(db, no_login=True)
    task_payload = {"shareurl": payload.shareurl, "account_name": account_name}
    adapter = manager.get_adapter_for_task(task_payload, allow_inactive=True)
    if adapter is None:
        raise not_found('DRIVE_ACCOUNT_NOT_FOUND', '没有可用的驱动账号')
    pwd_id, passcode, extracted_pdir_fid, _ = adapter.extract_url(payload.shareurl)
    if not pwd_id:
        raise bad_request('TASK_SHAREURL_INVALID', '无法解析分享链接')
    token_response = adapter.get_stoken(pwd_id, passcode or '')
    stoken = ((token_response or {}).get("data") or {}).get("stoken")
    # 提取分享者名称
    author_obj = ((token_response or {}).get("data") or {}).get("author")
    share_author_name = ""
    if isinstance(author_obj, dict):
        share_author_name = (
            author_obj.get("nick_name")
            or author_obj.get("nickname")
            or author_obj.get("user_name")
            or author_obj.get("name")
            or ""
        ).strip()
    if not stoken:
        message = (token_response or {}).get("message") or "获取分享 token 失败"
        raise bad_request('TASK_SHARE_TOKEN_FAILED', str(message))
    pdir_fid = payload.pdir_fid if payload.pdir_fid is not None else (extracted_pdir_fid or "")
    detail = adapter.get_detail(pwd_id, stoken, pdir_fid or "")
    if not isinstance(detail, dict) or detail.get("code") not in (None, 0, "0"):
        message = detail.get("message") if isinstance(detail, dict) else None
        raise bad_request('TASK_SHARE_DETAIL_FAILED', str(message or '读取分享目录失败，请重试'))
    data = (detail or {}).get("data") or {}
    if isinstance(data, dict):
        resolved = str(data.get("resolved_pdir_fid") or "").strip()
        if resolved:
            pdir_fid = resolved
    raw_items = (data.get("list") if isinstance(data, dict) else None) or []
    taskname = str(payload.taskname or "")
    pattern = str(payload.pattern or "")
    replace = str(payload.replace or "")
    savepath = str(payload.savepath or "").strip().rstrip("/")
    ignore_ext = bool(payload.ignore_extension)

    from app.services.tmdb_settings import get_or_create_tmdb_setting, get_tmdb_runtime_config

    tmdb_cfg = get_tmdb_runtime_config(get_or_create_tmdb_setting(db))
    tmdb_series_title = None
    tmdb_tv_seasons = None
    tmdb_year = None
    tmdb_id = int(payload.tmdb_id) if payload.tmdb_id is not None else 0
    tmdb_media_type = str(payload.tmdb_media_type or "").strip().lower()

    dir_file_list: list[dict] = []
    dir_filename_list: list[str] = []
    if savepath:
        dest_adapter = None
        account_row = get_active_account(db, account_name) if account_name else None
        if account_row is not None:
            cfg = AdapterRegistry.parse_config_json(account_row.drive_type, account_row.config_json, account_row.cookie)
            cookie = AdapterRegistry.serialize_config(account_row.drive_type, cfg)
            dest_adapter = AdapterFactory.create_adapter(
                account_row.drive_type,
                cookie,
                0,
                config=cfg,
                account_name=account_row.name,
                no_login=False,
            )
            if dest_adapter is not None:
                try:
                    ok = dest_adapter.ensure_initialized(
                        ttl_seconds=AdapterFactory.init_ttl_seconds()
                    )
                except Exception:
                    ok = None
                if not ok:
                    dest_adapter = None
        normalized = re.sub(r"/+", "/", savepath)
        dest_fid = None
        try:
            fid_list = (dest_adapter.get_fids([normalized]) if dest_adapter is not None else []) or []
            match = None
            for item in fid_list:
                item_path = item.get("file_path") or item.get("path") or item.get("filePath")
                if item_path == normalized:
                    match = item
                    break
            if match is None and fid_list:
                match = fid_list[0]
            if match and match.get("fid"):
                dest_fid = str(match.get("fid"))
        except Exception:
            dest_fid = None
        if dest_fid:
            listing = (dest_adapter.ls_dir(dest_fid, max_items=2000) if dest_adapter is not None else {}) or {}
            dir_file_list = (((listing or {}).get("data") or {}).get("list")) or []
            for raw in dir_file_list:
                if bool_is_dir(raw):
                    continue
                name = pick_name(raw)
                if name:
                    dir_filename_list.append(name)

    from app.services.magic_regex import get_enabled_magic_regex_map

    mr = MagicRename(magic_regex=get_enabled_magic_regex_map(db))
    mr.set_taskname(taskname)
    pattern, replace = mr.magic_regex_conv(pattern, replace)
    try:
        compiled_search = re.compile(pattern, re.IGNORECASE) if pattern else None
    except re.error as e:
        raise bad_request("TASK_REGEX_INVALID", f"pattern 正则不合法: {e}")
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

    def _is_video_name(name: str) -> bool:
        try:
            _base, _ext = os.path.splitext(str(name or ""))
        except Exception:
            return False
        return bool(_ext) and _ext.lower() in video_exts

    def _to_ts(v):
        try:
            return float(v)
        except Exception:
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
                        except Exception:
                            pass
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
        from app.extensions.runtime.drama_executor import _parse_size
        min_bytes = _parse_size(min_size_str)
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
        import logging as _ft_log
        _ft_dbg = _ft_log.getLogger(__name__)
        from datetime import datetime as _dt
        _ft_dbg.info("[file_time_filter] file_min_date=%r", file_min_date_str)
        for f in preview_list:
            if f.get("dir") or f.get("filtered_by_size") or f.get("filtered_by_keyword") or f.get("filtered_by_file_filter"):
                continue
            file_ts = f.get("updated_at")
            _ft_dbg.info("[file_time_filter] file=%r updated_at=%r type=%s", f.get("file_name"), file_ts, type(file_ts).__name__)
            if file_ts is not None:
                try:
                    if isinstance(file_ts, (int, float)) and file_ts > 0:
                        file_date = _dt.fromtimestamp(file_ts / 1000 if file_ts > 1e12 else file_ts).strftime("%Y-%m-%d")
                    else:
                        file_date = str(file_ts)[:10]
                    _ft_dbg.info("[file_time_filter] file_date=%r < min_date=%r -> %s", file_date, file_min_date_str, file_date < file_min_date_str)
                    if file_date < file_min_date_str:
                        f["file_name_re"] = None
                        f["file_name_saved"] = None
                        f["filtered_by_file_date"] = True
                except Exception as e:
                    _ft_dbg.info("[file_time_filter] error: %s", e)

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
    return SharePreviewOut(
        drive_type=str(drive_type),
        suggested_account_name=account_name,
        pwd_id=str(pwd_id),
        pdir_fid=str(pdir_fid or ""),
        share_author_name=share_author_name or None,
        items=items,
    )


@router.post("/share/preview-batch", response_model=SharePreviewBatchOut, dependencies=[Depends(require_permissions(TASK_READ))])
def post_share_preview_batch(payload: SharePreviewBatchIn, db: Session = Depends(get_db)):
    out, changed = preview_share_batch(db, payload)
    if changed:
        db.commit()
    return out


@router.post("/share/validate", response_model=ShareValidateOut, dependencies=[Depends(require_permissions(TASK_READ))])
def post_share_validate(payload: ShareValidateIn, db: Session = Depends(get_db)):
    """轻量级验证：只检查链接有效性 + 提取分享者信息"""
    return validate_share_links(db, payload.shareurls)


@router.post("/share/validate_stream", dependencies=[Depends(require_permissions(TASK_READ))])
def post_share_validate_stream(payload: ShareValidateIn, db: Session = Depends(get_db)):
    """流式验证：验证完一个链接立即返回结果"""
    from fastapi.responses import StreamingResponse
    from app.services.share_preview_batch import validate_share_links_streaming
    import json

    def event_generator():
        for item in validate_share_links_streaming(db, payload.shareurls):
            data = json.dumps({"shareurl": item.shareurl, "ok": item.ok, "share_author_name": item.share_author_name, "message": item.message}, ensure_ascii=False)
            yield f"data: {data}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
