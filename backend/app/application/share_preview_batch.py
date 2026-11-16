from __future__ import annotations

import logging
import os
import random
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)

from cachetools import TTLCache
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ApiError, not_found
from app.core.settings import settings
from app.core.runtime.tx import end_read_tx
from app.application.container import SessionLocal, drive_accounts_repo, system_settings_repo
from app.application.container import account_manager_cls as _acc_DatabaseAccountManager; DatabaseAccountManager = _acc_DatabaseAccountManager()
from app.application.container import adapter_registry as _acc_AdapterRegistry; AdapterRegistry = _acc_AdapterRegistry()
from app.application.dto.task_browse import SharePreviewBatchIn, SharePreviewBatchItemOut, SharePreviewBatchOut, ShareValidateOut
from app.application.invalid_share_links import upsert_invalid_share_link
from app.application.share_preview_batch_cache import (
    get_cached_preview_batch_item,
    purge_old_preview_batch_cache,
    upsert_preview_batch_cache,
)


_share_preview_batch_cache = TTLCache(
    maxsize=max(1, int(getattr(settings, "tasks_share_preview_batch_cache_max_entries", 2000) or 2000)),
    ttl=max(1, int(getattr(settings, "tasks_share_preview_batch_cache_ttl_seconds", 300) or 300)),
)
_share_preview_batch_cache_lock = threading.Lock()

# 验证结果缓存
_validate_cache_ttl = 300
_validate_cache: TTLCache[str, Any] = TTLCache(maxsize=2000, ttl=_validate_cache_ttl)
_validate_cache_lock = threading.Lock()


def _get_validate_cache_ttl() -> int:
    try:
        with SessionLocal() as db:
            value = system_settings_repo().get_value(db, "preview_cache_ttl_seconds", "")
            if value:
                return max(30, min(3600, int(value)))
    except Exception:
        pass
    return 300


def _update_validate_cache_ttl() -> None:
    global _validate_cache, _validate_cache_ttl
    ttl = _get_validate_cache_ttl()
    if ttl != _validate_cache_ttl:
        _validate_cache_ttl = ttl
        _validate_cache = TTLCache(maxsize=2000, ttl=ttl)


def cache_clear() -> None:
    with _share_preview_batch_cache_lock:
        _share_preview_batch_cache.clear()
    with _validate_cache_lock:
        _validate_cache.clear()


def _share_preview_batch_cache_get(*, shareurl: str) -> SharePreviewBatchItemOut | None:
    key = shareurl
    with _share_preview_batch_cache_lock:
        hit = _share_preview_batch_cache.get(key)
    if hit is None:
        return None
    return hit.model_copy()


def _share_preview_batch_cache_set(*, shareurl: str, item: SharePreviewBatchItemOut) -> None:
    key = shareurl
    with _share_preview_batch_cache_lock:
        _share_preview_batch_cache[key] = item


def _should_persist_invalid_share_link(message: str | None) -> bool:
    # Historical free-text negatives cannot establish permanent link failure.
    return message == _CONFIRMED_INVALID


_CONFIRMED_INVALID = '分享链接已确认失效'
_TRANSIENT_FAILURE = '网盘响应异常，可能为网络或账号状态问题，请稍后重试'


class _ShareReadError(Exception):
    def __init__(self, kind: str = 'transient'):
        self.kind = kind
        super().__init__({'invalid': _CONFIRMED_INVALID, 'auth': '账号登录状态异常，请重新认证',
                          'passcode': '分享提取码缺失或错误'}.get(kind, _TRANSIENT_FAILURE))


def _response_succeeded(response: Any) -> bool:
    return (isinstance(response, dict) and response.get('code') in (None, 0, '0')
            and response.get('status') in (None, 200, '200'))


def _token_failure(adapter: Any, response: Any) -> _ShareReadError:
    if not isinstance(response, dict) or response.get('status') not in (None, 200, '200', 400, '400'):
        return _ShareReadError()
    classify = getattr(adapter, 'classify_share_failure', None)
    if callable(classify):
        kind = classify(response).get('kind')
        # Require a documented provider code, never an arbitrary error message.
        if kind == 'invalid' and str(response.get('code')) in {
                str(code) for code in getattr(adapter, 'SHARE_INVALID_CODES', ())}:
            return _ShareReadError('invalid')
        if kind in ('auth', 'passcode'):
            return _ShareReadError(kind)
    return _ShareReadError()


@dataclass
class _BatchProbeResult:
    item: SharePreviewBatchItemOut
    confirmed_invalid: bool = False


def _bool_is_dir(payload: dict) -> bool:
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


def _pick_name(payload: dict) -> str:
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


def _pick_fid(payload: dict) -> str:
    return str(payload.get("fid") or payload.get("fs_id") or payload.get("file_id") or payload.get("id") or payload.get("fileId") or "")


def _pick_updated_at(payload: dict):
    return payload.get("updated_at") or payload.get("update_time") or payload.get("mtime") or payload.get("modified_at")


def _pick_size(payload: dict) -> int | None:
    if payload.get("size") is None:
        return None
    try:
        return int(payload.get("size"))
    except (TypeError, ValueError):
        return None


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


def _auto_resolve_latest_video(adapter, pwd_id: str, stoken: str, start_fid: str) -> tuple[str, dict | None]:
    current_fid = str(start_fid or "").strip()
    latest: dict | None = None
    for _depth in range(10):
        detail = adapter.get_detail(pwd_id, stoken, current_fid)
        if not _response_succeeded(detail) or not isinstance(detail.get('data'), dict):
            raise _ShareReadError()
        raw_items = detail['data'].get('list')
        if not isinstance(raw_items, list) or any(not isinstance(item, dict) for item in raw_items):
            raise _ShareReadError()
        files = [x for x in raw_items if not _bool_is_dir(x)]
        videos = [x for x in files if _is_video_name(_pick_name(x))]
        if videos:
            videos.sort(key=lambda x: _to_ts(_pick_updated_at(x)) or 0, reverse=True)
            hit = videos[0]
            latest = {
                "fid": _pick_fid(hit) or None,
                "name": _pick_name(hit) or None,
                "updated_at": _pick_updated_at(hit),
                "size": _pick_size(hit),
            }
            break
        dirs = [x for x in raw_items if _bool_is_dir(x)]
        if not dirs:
            break
        if len(dirs) > 1:
            dirs.sort(key=lambda x: _to_ts(_pick_updated_at(x)) or 0, reverse=True)
        next_dir = dirs[0]
        next_fid = str(_pick_fid(next_dir) or "").strip()
        if not next_fid or next_fid == current_fid:
            break
        current_fid = next_fid
    return current_fid, latest


def fetch_share_file_list(
    db: Session,
    shareurl: str,
    account_name: str | None = None,
    max_depth: int = 3,
    folder_filter: str = "",
    folder_exclude: str = "",
    folder_filter_mode: str = "",
    folder_exclude_mode: str = "",
    *, release_read: Callable[[], None] | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """获取分享链接的完整文件列表（只遍历前几层目录）"""
    from app.application.container import account_manager_cls as _acc_DatabaseAccountManager; DatabaseAccountManager = _acc_DatabaseAccountManager()

    drive_type = AdapterRegistry.detect_drive_type(shareurl)
    if drive_type is None:
        return [], False

    if not account_name:
        rows = drive_accounts_repo().list_enabled_by_type(db, drive_type=drive_type)
        rows.sort(
            key=lambda x: (
                0 if bool(getattr(x, "is_default", False)) else 1,
                0 if str(getattr(x, "runtime_status", "") or "") == "active" else 1,
                int(getattr(x, "id", 0) or 0),
            )
        )
        if not rows:
            return [], False
        account_name = str(getattr(rows[0], "name", "") or "").strip()

    manager = DatabaseAccountManager(db, no_login=True)
    task_payload = {"shareurl": shareurl, "account_name": account_name}
    adapter = manager.get_adapter_for_task(task_payload, allow_inactive=True)
    if adapter is None:
        return [], False

    if release_read is not None:
        release_read()

    if not bool(getattr(adapter, "is_active", False)):
        try:
            adapter.init()
        except Exception:
            return [], False

    try:
        pwd_id, passcode, extracted_pdir_fid, _ = adapter.extract_url(shareurl)
    except Exception:
        return [], False

    if not pwd_id:
        return [], False

    try:
        token_response = adapter.get_stoken(pwd_id, passcode or "")
    except Exception:
        return [], False

    stoken = ((token_response or {}).get("data") or {}).get("stoken")
    if not stoken:
        return [], False

    all_files: list[dict[str, Any]] = []
    changed = False
    folder_keywords = [kw.strip().lower() for kw in folder_filter.split("|") if kw.strip()] if folder_filter else []
    folder_exclude_keywords = [kw.strip().lower() for kw in folder_exclude.split("|") if kw.strip()] if folder_exclude else []
    folder_filter_is_any = str(folder_filter_mode or "all").strip().lower() == "any"
    folder_exclude_is_all = str(folder_exclude_mode or "any").strip().lower() == "all"

    def _collect_files(fid: str, depth: int, is_root: bool = True) -> None:
        nonlocal changed
        if depth > max_depth:
            return
        try:
            detail = adapter.get_detail(pwd_id, stoken, fid)
        except Exception:
            return
        raw_items = (((detail or {}).get("data") or {}).get("list")) or []
        if raw_items:
            changed = True
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            name = str(_pick_name(item) or "").strip()
            is_dir = bool(_bool_is_dir(item))
            if is_dir:
                sub_fid = str(_pick_fid(item) or "").strip()
                if not sub_fid or sub_fid == fid:
                    continue
                name_lower = name.lower()
                # 过滤：包含关键词的目录直接跳过，不进入
                if folder_exclude_keywords:
                    if folder_exclude_is_all:
                        if all(kw in name_lower for kw in folder_exclude_keywords):
                            continue
                    else:
                        if any(kw in name_lower for kw in folder_exclude_keywords):
                            continue
                # 递归进入子目录（无论是否匹配筛选词）
                _collect_files(sub_fid, depth + 1, is_root=False)
            else:
                all_files.append({
                    "file_name": name,
                    "is_dir": False,
                    "size": _pick_size(item),
                    "updated_at": _pick_updated_at(item),
                })

    _collect_files(extracted_pdir_fid or "", 1, is_root=True)
    return all_files, changed, extracted_pdir_fid


def fetch_share_file_list_grouped(
    db: Session,
    shareurl: str,
    account_name: str | None = None,
    max_depth: int = 3,
    folder_filter: str = "",
    folder_exclude: str = "",
    folder_filter_mode: str = "",
    folder_exclude_mode: str = "",
    folder_priority: str = "",
    folder_priority_mode: str = "",
    *, release_read: Callable[[], None] | None = None,
) -> list[tuple[list[dict[str, Any]], str, Any, str]]:
    """按子目录分组返回文件列表和目录标识。
    链接有 fid 时返回一组，无 fid 时每个子目录一组。"""
    from app.application.container import account_manager_cls as _acc_DatabaseAccountManager; DatabaseAccountManager = _acc_DatabaseAccountManager()

    drive_type = AdapterRegistry.detect_drive_type(shareurl)
    if drive_type is None:
        return []

    if not account_name:
        rows = drive_accounts_repo().list_enabled_by_type(db, drive_type=drive_type)
        rows.sort(
            key=lambda x: (
                0 if bool(getattr(x, "is_default", False)) else 1,
                0 if str(getattr(x, "runtime_status", "") or "") == "active" else 1,
                int(getattr(x, "id", 0) or 0),
            )
        )
        if not rows:
            return []
        account_name = str(getattr(rows[0], "name", "") or "").strip()

    manager = DatabaseAccountManager(db, no_login=True)
    task_payload = {"shareurl": shareurl, "account_name": account_name}
    adapter = manager.get_adapter_for_task(task_payload, allow_inactive=True)
    if adapter is None:
        return []

    if release_read is not None:
        release_read()

    if not bool(getattr(adapter, "is_active", False)):
        try:
            adapter.init()
        except Exception:
            return []

    try:
        pwd_id, passcode, extracted_pdir_fid, _ = adapter.extract_url(shareurl)
    except Exception:
        return []

    if not pwd_id:
        return []

    try:
        token_response = adapter.get_stoken(pwd_id, passcode or "")
    except Exception:
        return []

    stoken = ((token_response or {}).get("data") or {}).get("stoken")
    if not stoken:
        return []

    folder_keywords = [kw.strip().lower() for kw in folder_filter.split("|") if kw.strip()] if folder_filter else []
    folder_exclude_keywords = [kw.strip().lower() for kw in folder_exclude.split("|") if kw.strip()] if folder_exclude else []
    folder_filter_is_any = str(folder_filter_mode or "all").strip().lower() == "any"
    folder_exclude_is_all = str(folder_exclude_mode or "any").strip().lower() == "all"
    folder_priority_keywords = [kw.strip().lower() for kw in folder_priority.split("|") if kw.strip()] if folder_priority else []
    folder_priority_is_any = str(folder_priority_mode or "all").strip().lower() == "any"

    def _is_priority_match(dir_name: str) -> bool:
        """判断目录名是否匹配优先级关键词"""
        if not folder_priority_keywords or not dir_name:
            return False
        name_lower = dir_name.lower()
        if folder_priority_is_any:
            return any(kw in name_lower for kw in folder_priority_keywords)
        else:
            return all(kw in name_lower for kw in folder_priority_keywords)

    def _collect_from_dir(fid: str, dir_ts: Any = None, dir_name: str = "") -> list[tuple[list[dict[str, Any]], str, Any]]:
        """收集目录下的文件，按层级分组返回。
        当目录同时有文件和子目录时，文件单独一组，子目录各自递归成组。
        不匹配筛选词的目录仍会递归进入，但不收集该层的文件。"""

        def _walk(f: str, depth: int, f_ts: Any, f_name: str = "", is_root: bool = True) -> list[tuple[list[dict[str, Any]], str, Any]]:
            groups: list[tuple[list[dict[str, Any]], str, Any]] = []
            if depth > max_depth:
                return groups
            try:
                detail = adapter.get_detail(pwd_id, stoken, f)
            except Exception:
                return groups
            raw_items = (((detail or {}).get("data") or {}).get("list")) or []
            local_files: list[dict[str, Any]] = []
            # 判断当前目录是否匹配筛选词
            collect_files = True
            if folder_keywords and f_name:
                name_lower = f_name.lower()
                if folder_filter_is_any:
                    collect_files = any(kw in name_lower for kw in folder_keywords)
                else:
                    collect_files = all(kw in name_lower for kw in folder_keywords)
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                name = str(_pick_name(item) or "").strip()
                is_dir = bool(_bool_is_dir(item))
                if is_dir:
                    sub_fid = str(_pick_fid(item) or "").strip()
                    if not sub_fid or sub_fid == f:
                        continue
                    name_lower = name.lower()
                    # 过滤：包含关键词的目录直接跳过，不进入
                    if folder_exclude_keywords:
                        if folder_exclude_is_all:
                            if all(kw in name_lower for kw in folder_exclude_keywords):
                                continue
                        else:
                            if any(kw in name_lower for kw in folder_exclude_keywords):
                                continue
                    # 递归进入子目录（无论是否匹配筛选词）
                    groups.extend(_walk(sub_fid, depth + 1, _pick_updated_at(item), f_name=name, is_root=False))
                else:
                    local_files.append({
                        "file_name": name,
                        "is_dir": False,
                        "size": _pick_size(item),
                        "updated_at": _pick_updated_at(item),
                    })
            if local_files and collect_files:
                groups.append((local_files, f, f_ts, f_name))
            return groups

        return _walk(fid, 1, dir_ts, f_name=dir_name, is_root=True)

    def _sort_by_priority(groups: list[tuple[list[dict[str, Any]], str, Any]]) -> list[tuple[list[dict[str, Any]], str, Any]]:
        """按优先级排序：匹配关键词的文件夹排前面"""
        if not folder_priority_keywords:
            return groups
        
        priority_groups = []
        normal_groups = []
        for g in groups:
            # g 是 (files, fid, ts, dir_name) 的元组
            dir_name = g[3] if len(g) > 3 else ""
            if _is_priority_match(dir_name):
                priority_groups.append(g)
            else:
                normal_groups.append(g)
        
        # 优先级组排前面，保持各自内部顺序
        return priority_groups + normal_groups

    # 有 fid 的情况：跟原逻辑一样，返回一组
    if extracted_pdir_fid:
        groups = _collect_from_dir(extracted_pdir_fid)
        if groups:
            return _sort_by_priority(groups)
        return []

    # 无 fid 的情况：按根目录下的子目录分组
    try:
        detail = adapter.get_detail(pwd_id, stoken, "")
    except Exception:
        return []

    raw_items = (((detail or {}).get("data") or {}).get("list")) or []
    groups: list[tuple[list[dict[str, Any]], str, Any]] = []

    for item in raw_items:
        if not isinstance(item, dict):
            continue
        is_dir = bool(_bool_is_dir(item))
        if is_dir:
            sub_fid = str(_pick_fid(item) or "").strip()
            if not sub_fid:
                continue
            sub_name = str(_pick_name(item) or "").strip()
            sub_groups = _collect_from_dir(sub_fid, _pick_updated_at(item), dir_name=sub_name)
            for g in sub_groups:
                if g[0]:
                    groups.append(g)

    return _sort_by_priority(groups)


def _short_tx(fn):
    with SessionLocal() as s:
        s.expire_on_commit = False
        try:
            out = fn(s)
            s.commit()
            return out
        except Exception:
            s.rollback()
            raise


def _batch_error(error: Exception) -> str:
    if isinstance(error, ApiError):
        return error.message
    # Exception strings can contain cookies or request URLs. Unknown IO faults
    # also cannot establish that a share is permanently invalid.
    return '网盘响应异常，可能为网络或账号状态问题，请稍后重试'


def _purge_batch_cache() -> bool:
    try:
        return _short_tx(lambda db: purge_old_preview_batch_cache(db, retention_seconds=int(
            getattr(settings, "tasks_share_preview_batch_db_cache_retention_seconds", 7 * 24 * 60 * 60)
            or 7 * 24 * 60 * 60))) > 0
    except Exception:
        logger.warning("Preview batch cache cleanup failed")
        return False


def _classify_batch_cache(shareurls: list[str]) -> tuple[list[SharePreviewBatchItemOut], dict[str, list[str]], bool]:
    items: list[SharePreviewBatchItemOut] = []
    per_drive: dict[str, list[str]] = {}
    changed = False
    for url in shareurls:
        cached = _share_preview_batch_cache_get(shareurl=url)
        if cached is not None:
            items.append(cached)
            continue
        try:
            row, hit_changed = _short_tx(lambda db: get_cached_preview_batch_item(db, shareurl=url))
        except Exception:
            logger.warning("Preview batch persisted cache read failed")
            row, hit_changed = None, False
        if row is not None and not bool(row.ok) and _should_persist_invalid_share_link(row.message):
            out = SharePreviewBatchItemOut(shareurl=row.shareurl, drive_type=row.drive_type, ok=False, message=row.message)
            items.append(out)
            _share_preview_batch_cache_set(shareurl=url, item=out)
            changed = bool(changed or hit_changed)
            continue
        drive_type = (AdapterRegistry.detect_drive_type(url) or row.drive_type) if row is not None else AdapterRegistry.detect_drive_type(url)
        if drive_type is None:
            items.append(SharePreviewBatchItemOut(shareurl=url, drive_type=None, ok=False, message="无法识别的网盘分享链接"))
        else:
            per_drive.setdefault(str(drive_type), []).append(url)
    return items, per_drive, changed


def _batch_candidate_names(db: Session, drive_type: str, specified_name: str | None) -> list[str]:
    rows = drive_accounts_repo().list_enabled_by_type(db, drive_type=drive_type)
    rows.sort(key=lambda row: (0 if bool(getattr(row, 'is_default', False)) else 1,
        0 if str(getattr(row, 'runtime_status', '') or '') == 'active' else 1, int(getattr(row, 'id', 0) or 0)))
    if specified_name:
        specified = str(specified_name or '').strip()
        if specified:
            hit = next((row for row in rows if str(row.name) == specified), None)
            if hit is None:
                raise not_found('DRIVE_ACCOUNT_NOT_FOUND', '指定账号不存在或不可用')
            rows = [hit] + [row for row in rows if str(row.name) != specified]
    return [str(getattr(row, 'name', '') or '').strip() for row in rows]


class _BatchAdapters:
    """Adapter IO and per-batch instance reuse; receives no request Session."""
    def __init__(self, manager: Any):
        self.manager = manager
        self.cache: dict[str, Any] = {}

    def get_ready(self, name: str) -> Any:
        if not name:
            return None
        if name in self.cache:
            return self.cache[name]
        adapter = self.manager.manager.get_adapter(name)
        if adapter is None:
            return None
        if not bool(getattr(adapter, 'is_active', False)) and not bool(getattr(adapter, 'no_login', False)):
            try:
                if not adapter.init():
                    return None
            except Exception:
                return None
        self.cache[name] = adapter
        return adapter


def _probe_batch_url(url: str, drive_type: str, names: list[str], readers: _BatchAdapters) -> _BatchProbeResult:
    last_error: str | None = None
    confirmed_invalid = bool(names)
    author_name = ''
    for name in names:
        adapter = readers.get_ready(name)
        if adapter is None:
            confirmed_invalid = False
            last_error = f'账号 {name}: 不可用'
            continue
        try:
            pwd_id, passcode, extracted_fid, _ = adapter.extract_url(url)
        except Exception as error:
            confirmed_invalid = False
            last_error = _batch_error(error)
            break
        if not pwd_id:
            confirmed_invalid = False
            last_error = '无法解析分享链接'
            break
        try:
            token = adapter.get_stoken(pwd_id, passcode or '')
        except Exception as error:
            confirmed_invalid = False
            last_error = f'账号 {name}: {_batch_error(error)}'
            continue
        data = token.get('data') if isinstance(token, dict) else None
        data = data if isinstance(data, dict) else {}
        stoken = data.get('stoken')
        author = data.get('author')
        author_name = ((author.get('nick_name') or author.get('nickname') or author.get('user_name')
                        or author.get('name') or '').strip()) if isinstance(author, dict) else ''
        if not stoken or not _response_succeeded(token):
            failure = _token_failure(adapter, token)
            confirmed_invalid = confirmed_invalid and failure.kind == 'invalid'
            last_error = str(failure)
            continue
        try:
            folder_id, latest = _auto_resolve_latest_video(adapter, pwd_id, stoken, extracted_fid or '')
        except Exception as error:
            confirmed_invalid = False
            last_error = f'账号 {name}: {_batch_error(error)}'
            continue
        return _BatchProbeResult(SharePreviewBatchItemOut(shareurl=url, drive_type=drive_type, ok=True,
            suggested_account_name=name or None, pdir_fid=folder_id, resolved_pdir_fid=folder_id,
            latest_video=latest, share_author_name=author_name))
    return _BatchProbeResult(SharePreviewBatchItemOut(shareurl=url, drive_type=drive_type, ok=False,
        message=_CONFIRMED_INVALID if confirmed_invalid else (last_error or '没有可用账号')), confirmed_invalid)


def _persist_batch_result(out: SharePreviewBatchItemOut, *, confirmed_invalid: bool = False) -> tuple[bool, bool]:
    """Only independent short writes; a database fault never repeats drive IO."""
    cache_changed = invalid_changed = False
    if not out.ok and not confirmed_invalid:
        return False, False
    try:
        cache_changed = bool(_short_tx(lambda db: upsert_preview_batch_cache(db,
            shareurl=out.shareurl, drive_type=out.drive_type, ok=out.ok, message=out.message,
            ttl_seconds=int(getattr(settings, 'tasks_share_preview_batch_db_cache_ttl_seconds', 6 * 60 * 60)
                            or 6 * 60 * 60))))
    except Exception:
        logger.warning('Preview batch cache write failed')
    if not out.ok and confirmed_invalid:
        try:
            invalid_changed = bool(_short_tx(lambda db: upsert_invalid_share_link(db,
                shareurl=out.shareurl, drive_type=out.drive_type, message=out.message)))
        except Exception:
            logger.warning('Preview batch invalid-link write failed')
    return cache_changed, invalid_changed


def _read_batch_accounts(db: Session, payload: SharePreviewBatchIn, per_drive: dict[str, list[str]]) -> tuple[_BatchAdapters, dict[str, list[str]], dict[str, str]]:
    manager = DatabaseAccountManager(db, no_login=True)
    candidates: dict[str, list[str]] = {}
    errors: dict[str, str] = {}
    for drive in per_drive:
        try:
            candidates[drive] = _batch_candidate_names(db, drive, payload.account_name)
        except Exception as error:
            errors[drive] = _batch_error(error)
    # Account names are detached before ending the read transaction; external
    # stages cannot lazily reload ORM attributes on the request Session.
    end_read_tx(db)
    return _BatchAdapters(manager), candidates, errors


def preview_share_batch(db: Session, payload: SharePreviewBatchIn) -> tuple[SharePreviewBatchOut, bool]:
    shareurls = list(dict.fromkeys(str(url or '').strip() for url in (payload.shareurls or []) if str(url or '').strip()))
    if not shareurls:
        return SharePreviewBatchOut(items=[]), False
    if db.new or db.dirty or db.deleted:
        raise RuntimeError('Batch preview requires a read-only session')
    end_read_tx(db)
    cache_changed = _purge_batch_cache()
    items, per_drive, cache_hit_changed = _classify_batch_cache(shareurls)
    cache_changed = bool(cache_changed or cache_hit_changed)
    if not per_drive:
        return SharePreviewBatchOut(items=items), cache_changed
    readers, candidates, errors = _read_batch_accounts(db, payload, per_drive)
    invalid_changed = False
    for drive, urls in per_drive.items():
        if drive in errors or not candidates.get(drive):
            message = errors.get(drive, '没有可用的驱动账号')
            items.extend(SharePreviewBatchItemOut(shareurl=url, drive_type=drive, ok=False, message=message) for url in urls)
            continue
        for url in urls:
            try:
                result = _probe_batch_url(url, drive, candidates[drive], readers)
            except Exception as error:
                result = _BatchProbeResult(SharePreviewBatchItemOut(shareurl=url, drive_type=drive, ok=False, message=_batch_error(error)))
            out = result.item
            items.append(out)
            if out.ok or result.confirmed_invalid:
                _share_preview_batch_cache_set(shareurl=url, item=out)
            saved_cache, saved_invalid = _persist_batch_result(out, confirmed_invalid=result.confirmed_invalid)
            cache_changed = bool(cache_changed or saved_cache)
            invalid_changed = bool(invalid_changed or saved_invalid)
            time.sleep(random.uniform(0.06, 0.22))
    return SharePreviewBatchOut(items=items), bool(invalid_changed or cache_changed)


def validate_share_links_streaming(db: Session, shareurls: list[str], *, release_read: Callable[[], None] | None = None):
    """生成器版本：验证完一个链接立即返回结果，不等全部完成。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from app.application.dto.task_browse import ShareValidateItemOut
    from app.application.system_settings import get_setting as _get_setting

    urls = [str(x or "").strip() for x in shareurls]
    urls = [x for x in urls if x]
    urls = list(dict.fromkeys(urls))
    if not urls:
        return

    # 缓存查找
    _update_validate_cache_ttl()
    cached_items: list[ShareValidateItemOut] = []
    uncached_urls: list[str] = []
    with _validate_cache_lock:
        for url in urls:
            hit = _validate_cache.get(url)
            if hit is not None:
                cached_items.append(hit)
            else:
                uncached_urls.append(url)

    for item in cached_items:
        yield item

    if not uncached_urls:
        return

    def _safe_error(e: Exception) -> str:
        text = f"{type(e).__name__}: {str(e)}"
        return text.strip() or type(e).__name__

    # 非夸克网盘直接返回，无需验证
    per_drive: dict[str, list[str]] = {}
    for url in uncached_urls:
        drive_type = AdapterRegistry.detect_drive_type(url)
        if drive_type is None:
            yield ShareValidateItemOut(shareurl=url, ok=False, message="无法识别的网盘分享链接")
            continue
        # 非夸克网盘直接返回，无需验证（避免多线程 session 冲突）
        if drive_type == "quark":
            per_drive.setdefault(str(drive_type), []).append(url)
        else:
            yield ShareValidateItemOut(shareurl=url, ok=True)

    if not per_drive:
        return

    # 预先查询所有可用账号（避免线程内访问数据库）
    accounts_by_drive: dict[str, list[str]] = {}
    for drive_type in per_drive:
        rows = drive_accounts_repo().list_enabled_by_type(db, drive_type=drive_type)
        rows.sort(key=lambda x: (
            0 if bool(getattr(x, "is_default", False)) else 1,
            0 if str(getattr(x, "runtime_status", "") or "") == "active" else 1,
            int(getattr(x, "id", 0) or 0),
        ))
        accounts_by_drive[drive_type] = [str(row.name or "").strip() for row in rows]

    manager = DatabaseAccountManager(db, no_login=True)

    try:
        batch_size = int(str(_get_setting(db, "validate_batch_size") or "5").strip())
    except (TypeError, ValueError):
        batch_size = 5
    batch_size = max(1, min(batch_size, 20))
    if release_read is not None:
        release_read()

    def _validate_single_url(url: str, drive_type: str) -> ShareValidateItemOut:
        """验证单个链接"""
        candidates = accounts_by_drive.get(drive_type, [])
        if not candidates:
            return ShareValidateItemOut(shareurl=url, ok=False, message="没有可用的驱动账号")

        ok = False
        author_name = ""
        last_error: str | None = None
        for name in candidates:
            if not name:
                last_error = "账号名称为空"
                continue
            adapter = manager.manager.get_adapter(name)
            if adapter is None:
                last_error = f"账号 {name}: 不可用"
                continue
            if (not bool(getattr(adapter, "is_active", False))) and (not bool(getattr(adapter, "no_login", False))):
                try:
                    init_ok = adapter.init()
                except Exception:
                    init_ok = None
                if not init_ok:
                    last_error = f"账号 {name}: 初始化失败"
                    continue
            try:
                pwd_id, passcode, pdir_fid, _ = adapter.extract_url(url)
            except Exception as e:
                last_error = _safe_error(e)
                break
            if not pwd_id:
                last_error = "无法解析分享链接"
                break
            try:
                token_response = adapter.get_stoken(pwd_id, passcode or "")
            except Exception as e:
                last_error = f"账号 {name}: {_safe_error(e)}"
                continue
            stoken = ((token_response or {}).get("data") or {}).get("stoken")
            author_obj = ((token_response or {}).get("data") or {}).get("author")
            if isinstance(author_obj, dict):
                author_name = (
                    author_obj.get("nick_name")
                    or author_obj.get("nickname")
                    or author_obj.get("user_name")
                    or author_obj.get("name")
                    or ""
                ).strip()
            if not stoken:
                message = (token_response or {}).get("message") or "获取分享 token 失败"
                last_error = f"账号 {name}: {str(message)}"
                continue
            try:
                detail = adapter.get_detail(pwd_id, stoken, pdir_fid or "")
            except Exception as e:
                last_error = f"账号 {name}: {_safe_error(e)}"
                continue
            raw_items = (((detail or {}).get("data") or {}).get("list")) or []
            if raw_items:
                ok = True
                break
            else:
                last_error = detail.get("message") if isinstance(detail, dict) and detail.get("message") else "链接内容为空"
                continue

        result = ShareValidateItemOut(
            shareurl=url,
            ok=ok,
            share_author_name=author_name or None,
            message=None if ok else (last_error or "没有可用账号"),
        )
        # 缓存
        with _validate_cache_lock:
            _validate_cache[url] = result
        return result

    # 并行验证，完成一个立即返回一个
    for drive_type, dt_urls in per_drive.items():
        if not accounts_by_drive.get(drive_type):
            for url in dt_urls:
                yield ShareValidateItemOut(shareurl=url, ok=False, message="没有可用的驱动账号")
            continue
        with ThreadPoolExecutor(max_workers=batch_size) as executor:
            future_map = {executor.submit(_validate_single_url, url, drive_type): url for url in dt_urls}
            for future in as_completed(future_map):
                try:
                    result = future.result()
                    yield result
                except Exception as e:
                    url = future_map[future]
                    yield ShareValidateItemOut(shareurl=url, ok=False, message=_safe_error(e))


def validate_share_links(db: Session, shareurls: list[str], *, release_read: Callable[[], None] | None = None) -> ShareValidateOut:
    """轻量级验证：只检查链接有效性 + 提取分享者信息，不做视频解析。"""
    from app.application.dto.task_browse import ShareValidateOut
    items = list(validate_share_links_streaming(db, shareurls, **({"release_read": release_read} if release_read is not None else {})))
    return ShareValidateOut(items=items)

def fetch_share_root_dirs(db: Session, shareurl: str, account_name: str | None = None, *, release_read: Callable[[], None] | None = None) -> list[tuple[str, str]]:
    """返回分享根目录下的第一层子目录 [(fid, name)]，用于文件夹对比模式定位层级。"""
    from app.application.container import account_manager_cls as _acc_DatabaseAccountManager; DatabaseAccountManager = _acc_DatabaseAccountManager()

    drive_type = AdapterRegistry.detect_drive_type(shareurl)
    if drive_type is None:
        return []

    if not account_name:
        rows = drive_accounts_repo().list_enabled_by_type(db, drive_type=drive_type)
        rows.sort(
            key=lambda x: (
                0 if bool(getattr(x, "is_default", False)) else 1,
                0 if str(getattr(x, "runtime_status", "") or "") == "active" else 1,
                int(getattr(x, "id", 0) or 0),
            )
        )
        if not rows:
            return []
        account_name = str(getattr(rows[0], "name", "") or "").strip()

    manager = DatabaseAccountManager(db, no_login=True)
    adapter = manager.get_adapter_for_task(
        {"shareurl": shareurl, "account_name": account_name}, allow_inactive=True
    )
    if adapter is None:
        return []
    if release_read is not None:
        release_read()

    if not bool(getattr(adapter, "is_active", False)):
        try:
            adapter.init()
        except Exception:
            return []

    try:
        pwd_id, passcode, extracted_pdir_fid, _ = adapter.extract_url(shareurl)
    except Exception:
        return []
    if not pwd_id:
        return []

    try:
        token_response = adapter.get_stoken(pwd_id, passcode or "")
    except Exception:
        return []
    stoken = ((token_response or {}).get("data") or {}).get("stoken")
    if not stoken:
        return []

    fid = str(extracted_pdir_fid or "").strip()
    try:
        detail = adapter.get_detail(pwd_id, stoken, fid)
    except Exception:
        return []

    raw_items = (((detail or {}).get("data") or {}).get("list")) or []
    out: list[tuple[str, str]] = []
    has_root_files = False
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        if not _bool_is_dir(item):
            has_root_files = True
            continue
        sub_fid = str(_pick_fid(item) or "").strip()
        if not sub_fid:
            continue
        out.append((sub_fid, str(_pick_name(item) or "").strip()))
    # 根目录本身就有文件时不再下钻，否则会漏掉根目录的文件
    if has_root_files:
        return []
    return out

