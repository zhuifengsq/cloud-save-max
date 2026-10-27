from __future__ import annotations

import inspect
import logging
import re
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable, TypeVar

from treelib import Tree

from app.core.errors import bad_request
from app.core.settings import settings
from app.extensions.runtime.execution_log import ExecutionLog
from app.extensions.runtime.magic_rename import MagicRename
from app.extensions.runtime.retry_utils import RetryResult, retry_call, summarize_payload


logger = logging.getLogger(__name__)


class SkipTask(Exception):
    pass


T = TypeVar("T")


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
            except (ValueError, TypeError):
                return None
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def _validate_code_status(action: str, resp: Any) -> RetryResult:
    if not isinstance(resp, dict):
        return RetryResult(value=resp, ok=True)
    status = resp.get("status")
    if status is not None and status not in (200, "200"):
        return RetryResult(
            value=resp,
            ok=False,
            error_message=f"{action} status={status} message={resp.get('message') or ''} resp={summarize_payload(resp)}",
        )
    code = resp.get("code")
    if code is not None and code not in (0, "0"):
        return RetryResult(
            value=resp,
            ok=False,
            error_message=f"{action} code={code} message={resp.get('message') or ''} resp={summarize_payload(resp)}",
        )
    return RetryResult(value=resp, ok=True)


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


class DramaTaskExecutor:
    def __init__(self, *, adapter: Any, task_data: dict[str, Any], log: ExecutionLog | None = None):
        self.adapter = adapter
        self.task_data = task_data
        self.log = log
        self.transfer_count = 0
        self.saved_latest: dict[str, Any] | None = None

    def _set_stage(self, stage: str | None) -> None:
        if self.log:
            self.log.set_stage(stage)

    def _section(self, title: str) -> None:
        if self.log:
            self.log.section(title)

    def _line(self, text: str = "") -> None:
        if self.log:
            self.log.line(text)

    def _retry(
        self,
        *,
        action: str,
        fn: Callable[[], T],
        validate: Callable[[T], RetryResult] | None = None,
    ) -> T:
        return retry_call(
            action=action,
            fn=fn,
            validate=validate,
            attempts=int(getattr(settings, "drama_runtime_retry_max_attempts", 3) or 3),
            backoff_seconds=float(getattr(settings, "drama_runtime_retry_backoff_seconds", 1.0) or 1.0),
            max_backoff_seconds=float(getattr(settings, "drama_runtime_retry_max_backoff_seconds", 8.0) or 8.0),
            jitter_ratio=float(getattr(settings, "drama_runtime_retry_jitter_ratio", 0.2) or 0.2),
            emit=self._line,
            log=logger,
        )

    def _ls_dir(self, pdir_fid: str) -> dict[str, Any]:
        return self._retry(
            action="ls_dir",
            fn=lambda: self.adapter.ls_dir(pdir_fid, max_items=0) or {},
            validate=lambda r: _validate_code_status("ls_dir", r),
        )

    def _mkdir(self, savepath: str) -> dict[str, Any]:
        return self._retry(
            action="mkdir",
            fn=lambda: self.adapter.mkdir(savepath),
            validate=lambda r: _validate_code_status("mkdir", r),
        )

    def _query_task(self, job_id: str) -> dict[str, Any]:
        return self._retry(
            action="query_task",
            fn=lambda: self.adapter.query_task(str(job_id)) or {},
            validate=lambda r: _validate_code_status("query_task", r),
        )

    def _try_get_dir_fid(self, dir_path: str) -> str | None:
        fids = self._retry(action="get_fids", fn=lambda: self.adapter.get_fids([dir_path]) or [])
        for item in fids:
            if (item.get("file_path") or item.get("path")) == dir_path and item.get("fid"):
                return str(item["fid"])
        if fids and fids[0].get("fid"):
            return str(fids[0]["fid"])
        return None

    def _ensure_dest_dir_fid(self, savepath: str) -> str:
        existing = self._try_get_dir_fid(savepath)
        if existing:
            return existing
        response = self._mkdir(savepath)
        if not response:
            raise RuntimeError("创建目录失败")
        created = self._try_get_dir_fid(savepath)
        if created:
            return created
        raise RuntimeError("创建目录失败")

    def _list_dest_names(self, dest_fid: str, ignore_extension: bool) -> set[str]:
        listing = self._ls_dir(dest_fid)
        raw_items = (((listing or {}).get("data") or {}).get("list")) or []
        names: set[str] = set()
        for raw in raw_items:
            if _is_dir(raw):
                continue
            name = _get_name(raw)
            if not name:
                continue
            names.add(_normalize_name(name, ignore_extension))
        return names

    def _fetch_share_items(self, *, pwd_id: str, stoken: str, pdir_fid: str) -> list[dict[str, Any]]:
        def _validate(detail: dict[str, Any]) -> RetryResult:
            if not isinstance(detail, dict):
                return RetryResult(value=detail, ok=False, error_message=f"get_detail invalid response: {summarize_payload(detail)}")
            status = detail.get("status")
            if status is not None and status not in (200, "200"):
                return RetryResult(value=detail, ok=False, error_message=f"get_detail status={status} message={detail.get('message') or ''} resp={summarize_payload(detail)}")
            code = detail.get("code")
            if code is not None and code not in (0, "0"):
                return RetryResult(value=detail, ok=False, error_message=f"get_detail code={code} message={detail.get('message') or ''} resp={summarize_payload(detail)}")
            data = detail.get("data")
            if not isinstance(data, dict) or data.get("list") is None:
                return RetryResult(value=detail, ok=False, error_message=f"get_detail missing list: {summarize_payload(detail)}")
            return RetryResult(value=detail, ok=True)

        detail = self._retry(action="get_detail", fn=lambda: self.adapter.get_detail(pwd_id, stoken, pdir_fid or ""), validate=_validate)
        return (((detail or {}).get("data") or {}).get("list")) or []

    def _list_dest_dir_map(self, dest_fid: str) -> dict[str, str]:
        listing = self._ls_dir(dest_fid)
        raw_items = (((listing or {}).get("data") or {}).get("list")) or []
        result: dict[str, str] = {}
        for raw in raw_items:
            if not _is_dir(raw):
                continue
            name = _get_name(raw)
            fid = _get_fid(raw)
            if name and fid:
                result[name] = fid
        return result

    def _save_items(self, *, pwd_id: str, stoken: str, to_pdir_fid: str, items: list[dict[str, Any]]) -> None:
        plan: list[DramaPlanItem] = []
        for raw in items:
            fid = str(_get_fid(raw)).strip()
            token = _get_fid_token(raw)
            name = _get_name(raw)
            if not fid:
                continue
            if not token:
                raise RuntimeError("分享列表缺少 fid_token，无法转存")
            plan.append(
                DramaPlanItem(
                    fid=fid,
                    fid_token=str(token),
                    origin_name=str(name),
                    target_name=str(name),
                )
            )
        if not plan:
            return
        self._save_with_saved_fids(pwd_id=pwd_id, stoken=stoken, dest_root_fid=to_pdir_fid, plan=plan)

    def _call_save_file(
        self,
        *,
        fid_list: list[str],
        fid_token_list: list[str],
        to_pdir_fid: str,
        pwd_id: str,
        stoken: str,
        file_names: list[str],
    ) -> dict[str, Any]:
        try:
            params = inspect.signature(self.adapter.save_file).parameters
            if "file_names" in params:
                return self.adapter.save_file(fid_list, fid_token_list, to_pdir_fid, pwd_id, stoken, file_names=file_names)
        except Exception:
            pass
        try:
            return self.adapter.save_file(fid_list, fid_token_list, to_pdir_fid, pwd_id, stoken, file_names=file_names)
        except TypeError:
            return self.adapter.save_file(fid_list, fid_token_list, to_pdir_fid, pwd_id, stoken)

    def _extract_saved_fids(
        self,
        *,
        save_file_return: dict[str, Any] | None,
        query_task_return: dict[str, Any] | None,
    ) -> tuple[list[str], str]:
        data = (save_file_return or {}).get("data") or {}
        if data.get("_sync") and data.get("save_as_top_fids") is not None:
            fids = [str(x).strip() for x in (data.get("save_as_top_fids") or []) if str(x).strip()]
            return fids, "save_file.data.save_as_top_fids"

        drive_type = str(getattr(self.adapter, "DRIVE_TYPE", "") or "")
        qdata = (query_task_return or {}).get("data") or {}
        save_as = (qdata.get("save_as") or {}) if isinstance(qdata, dict) else {}
        if drive_type == "uc":
            if save_as.get("save_as_select_top_fids") is not None:
                fids = [str(x).strip() for x in (save_as.get("save_as_select_top_fids") or []) if str(x).strip()]
                return fids, "query_task.data.save_as.save_as_select_top_fids"
        if save_as.get("save_as_top_fids") is not None:
            fids = [str(x).strip() for x in (save_as.get("save_as_top_fids") or []) if str(x).strip()]
            return fids, "query_task.data.save_as.save_as_top_fids"
        return [], "missing"

    def _save_with_saved_fids(self, *, pwd_id: str, stoken: str, dest_root_fid: str, plan: list[DramaPlanItem]) -> list[str]:
        fid_list = [item.fid for item in plan]
        fid_token_list = [item.fid_token or "" for item in plan]
        file_names = [item.origin_name for item in plan]
        if any(not token for token in fid_token_list):
            raise RuntimeError("分享列表缺少 fid_token，无法转存")

        drive_type = str(getattr(self.adapter, "DRIVE_TYPE", "") or "")
        actual_save_fid = str(dest_root_fid)
        share_folder_fid = None
        if drive_type == "uc" and hasattr(self.adapter, "get_or_create_share_folder") and hasattr(self.adapter, "move_files_to_target"):
            share_folder_fid = self.adapter.get_or_create_share_folder()
            if share_folder_fid:
                actual_save_fid = str(share_folder_fid)
                self._line("使用中转目录: 来自：分享")
            else:
                self._line("获取中转目录失败，直接转存到目标目录")

        saved_fids: list[str] = []
        err_msg: str | None = None
        idx = 0
        while idx < len(fid_list):
            batch_fids = fid_list[idx : idx + 100]
            batch_tokens = fid_token_list[idx : idx + 100]
            batch_names = file_names[idx : idx + 100]
            idx += 100

            save_ret = self._call_save_file(
                fid_list=batch_fids,
                fid_token_list=batch_tokens,
                to_pdir_fid=actual_save_fid,
                pwd_id=str(pwd_id),
                stoken=str(stoken),
                file_names=batch_names,
            ) or {}
            if (save_ret.get("code") not in (0, "0", None)) or (save_ret.get("status") not in (200, "200", None)):
                err_msg = str(save_ret.get("message") or "转存失败")
                break

            qret = None
            job_id = (
                (save_ret.get("data") or {}).get("task_id")
                or (save_ret.get("data") or {}).get("taskId")
                or save_ret.get("task_id")
                or save_ret.get("taskId")
                or ""
            ).strip()
            if job_id:
                self._line(f"转存任务 id: {job_id}")
                qret = self._query_task(str(job_id))
                status = ((qret.get("data") or {}).get("status"))
                if status not in (2, "2", None):
                    err_msg = str((qret.get("data") or {}).get("message") or qret.get("message") or "转存任务失败")
                    break

            batch_saved, source = self._extract_saved_fids(save_file_return=save_ret, query_task_return=qret)
            self._line(f"saved_fids: {len(batch_saved)}（来源={source}）")
            saved_fids.extend(batch_saved)

        if err_msg:
            raise RuntimeError(err_msg)
        if not saved_fids:
            raise RuntimeError("转存任务完成但未返回转存后 fid 列表")

        if share_folder_fid and drive_type == "uc":
            self._set_stage("move_files")
            self._section("移动到目标目录")
            self._line(f"移动文件数: {len(saved_fids)}")
            move_ret = self.adapter.move_files_to_target(saved_fids, str(dest_root_fid)) or {}
            if move_ret.get("code") not in (0, "0", None):
                raise RuntimeError(str(move_ret.get("message") or "移动失败"))

        if len(saved_fids) != len(plan):
            self._line(f"提示: saved_fids={len(saved_fids)} 与计划数={len(plan)} 不一致，将仅对齐前 {min(len(saved_fids), len(plan))} 项")
        if saved_fids:
            self.transfer_count += len(saved_fids)
        return saved_fids

    def _iter_files(self, items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for raw in items:
            if _is_dir(raw):
                continue
            fid = _get_fid(raw)
            name = _get_name(raw)
            if not fid or not name:
                continue
            result.append(raw)
        return result

    def _plan_transfer(self, *, share_files: list[dict[str, Any]], dest_file_list: list[dict[str, Any]]) -> list[DramaPlanItem]:
        taskname = str(self.task_data.get("taskname") or "")
        pattern = str(self.task_data.get("pattern") or "")
        replace = str(self.task_data.get("replace") or "")
        ignore_extension = bool(self.task_data.get("ignore_extension"))

        mr = MagicRename(magic_regex=(self.task_data.get("magic_regex") if isinstance(self.task_data.get("magic_regex"), dict) else None))
        mr.set_taskname(taskname)
        pattern, replace = mr.magic_regex_conv(pattern, replace)
        compiled_search = re.compile(pattern, re.IGNORECASE) if pattern else None
        tmdb_series_title = str(self.task_data.get("tmdb_series_title") or "").strip() or None
        tmdb_tv_seasons = self.task_data.get("tmdb_tv_seasons") if isinstance(self.task_data.get("tmdb_tv_seasons"), list) else None
        tmdb_year = int(self.task_data.get("tmdb_year")) if isinstance(self.task_data.get("tmdb_year"), int) else None
        tmdb_media_type = str(self.task_data.get("tmdb_media_type") or "").strip().lower()

        def _to_ts(v):
            try:
                return float(v)
            except Exception:
                return None

        dest_filename_list = []
        dest_episode_set: set[tuple[int, int]] = set()
        from app.services.episode_number import extract_episode
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

        def _to_size(v):
            try:
                return int(v)
            except Exception:
                return None

        # 最小文件大小过滤
        addition = self.task_data.get("addition") or {}
        min_size_str = str(addition.get("min_size") or "").strip()
        if min_size_str:
            min_bytes = _parse_size(min_size_str)
            if min_bytes is not None and min_bytes > 0:
                before = len(candidates)
                candidates = [f for f in candidates if (_to_size(f.get("size")) or 0) >= min_bytes]
                removed = before - len(candidates)
                if removed:
                    self._line(f"大小过滤：移除 {removed} 个低于 {min_size_str} 的文件")

        # 关键词过滤
        filter_words_str = str(addition.get("filter_words") or "").strip()
        if filter_words_str:
            filter_words = [w.strip().lower() for w in filter_words_str.split("|") if w.strip()]
            if filter_words:
                before = len(candidates)
                candidates = [
                    f for f in candidates
                    if not any(w in _get_name(f).lower() for w in filter_words)
                ]
                removed = before - len(candidates)
                if removed:
                    self._line(f"关键词过滤：移除 {removed} 个匹配的文件")

        # 文件关键词筛选
        file_filter_str = str(addition.get("file_filter") or "").strip()
        if file_filter_str:
            file_filter_words = [w.strip().lower() for w in file_filter_str.split("|") if w.strip()]
            if file_filter_words:
                file_filter_mode = str(addition.get("file_filter_mode") or "all").strip().lower()
                before = len(candidates)
                if file_filter_mode == "any":
                    candidates = [
                        f for f in candidates
                        if any(w in _get_name(f).lower() for w in file_filter_words)
                    ]
                else:
                    candidates = [
                        f for f in candidates
                        if all(w in _get_name(f).lower() for w in file_filter_words)
                    ]
                removed = before - len(candidates)
                if removed:
                    self._line(f"文件筛选：移除 {removed} 个不匹配的文件")

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

    def _rename_one(self, *, fid: str, origin: str, target: str) -> bool:
        fid = str(fid or "").strip()
        origin = str(origin or "")
        target = str(target or "")
        if not fid or not origin or not target or origin == target:
            return True
        try:
            self._retry(
                action="rename",
                fn=lambda: self.adapter.rename(fid, target),
                validate=lambda r: _validate_code_status("rename", r),
            )
            return True
        except Exception as exc:
            self._line(f"FAIL: 重命名失败 {origin} → {target} err={str(exc).strip() or type(exc).__name__}")
            return False

    def _rename_by_fids(self, *, saved_fids: list[str], plan: list[DramaPlanItem], dest_root_fid: str) -> None:
        dest_root_fid = str(dest_root_fid or "").strip()
        attempted: list[tuple[str, str, str]] = []
        failed: set[str] = set()

        limit = min(len(saved_fids), len(plan))
        for i in range(limit):
            fid = str(saved_fids[i] or "").strip()
            if not fid:
                continue
            origin = str(plan[i].origin_name or "")
            target = str(plan[i].target_name or "")
            if not origin or not target or origin == target:
                continue
            attempted.append((fid, origin, target))
            self._line(f"重命名：{origin} → {target}")
            if not self._rename_one(fid=fid, origin=origin, target=target):
                failed.add(fid)

        if not attempted or not dest_root_fid:
            return

        fid_name: dict[str, str] = {}
        try:
            listing = self._ls_dir(dest_root_fid)
            items = (((listing or {}).get("data") or {}).get("list")) or []
            for raw in items:
                if _is_dir(raw):
                    continue
                fid = str(_get_fid(raw) or "").strip()
                if not fid:
                    continue
                fid_name[fid] = str(_get_name(raw) or "")
        except Exception as exc:
            self._line(f"INFO: 重命名校验失败，无法读取目标目录 err={str(exc).strip() or type(exc).__name__}")
            fid_name = {}

        need_retry: list[tuple[str, str, str]] = []
        for fid, origin, target in attempted:
            current = fid_name.get(fid)
            if current == target:
                continue
            need_retry.append((fid, origin, target))

        retry_success = 0
        retry_failed = 0
        if need_retry:
            self._line(f"二次校验：待重试 {len(need_retry)}/{len(attempted)}")
            for fid, origin, target in need_retry:
                self._line(f"重试重命名：{origin} → {target}")
                if self._rename_one(fid=fid, origin=origin, target=target):
                    retry_success += 1
                else:
                    retry_failed += 1

        final_ok = 0
        final_failed = 0
        try:
            listing = self._ls_dir(dest_root_fid)
            items = (((listing or {}).get("data") or {}).get("list")) or []
            fid_name = {}
            for raw in items:
                if _is_dir(raw):
                    continue
                fid = str(_get_fid(raw) or "").strip()
                if not fid:
                    continue
                fid_name[fid] = str(_get_name(raw) or "")
            for fid, _origin, target in attempted:
                if fid_name.get(fid) == target:
                    final_ok += 1
                else:
                    final_failed += 1
        except Exception:
            final_ok = len(attempted) - len(failed)
            final_failed = len(failed)

        self._line(
            f"重命名摘要: total={len(attempted)} ok={final_ok} failed={final_failed} retry_ok={retry_success} retry_failed={retry_failed}"
        )



    def execute(self) -> Tree:
        extra = self.task_data.get("extra") or {}
        allow_once = bool(extra.get("allow_once"))
        runweek_mode = str(extra.get("runweek_mode") or "manual").strip().lower()
        runweek = extra.get("runweek") or []
        now = datetime.now()
        self._set_stage("validate_schedule")
        self._section("验证调度条件")
        self._line(f"执行时间: {now.strftime('%Y-%m-%d %H:%M:%S')}")
        if allow_once:
            self._line("运行一次: 忽略按星期运行限制")
        elif runweek_mode == "auto":
            tmdb_id = int(self.task_data.get("tmdb_id") or 0)
            tmdb_media_type = str(self.task_data.get("tmdb_media_type") or "").strip().lower()
            if tmdb_id <= 0 or tmdb_media_type != "tv":
                self._line("跳过: 自动识别运行星期需要绑定 TMDB TV")
                raise SkipTask("未绑定 TMDB TV")
            if not bool(self.task_data.get("tmdb_configured")):
                self._line("跳过: TMDB 未配置，无法自动识别运行星期")
                raise SkipTask("TMDB 未配置")
            days = self.task_data.get("tmdb_episode_weekdays") or self.task_data.get("tmdb_update_weekdays") or []
            try:
                week = set(int(x) for x in (days or []))
            except Exception:
                week = set()
            week = {int(x) for x in week if 1 <= int(x) <= 7}
            if not week:
                self._line("跳过: 无法自动识别运行星期")
                raise SkipTask("无法自动识别运行星期")
            if now.isoweekday() not in week:
                self._line(f"跳过: 星期 {now.isoweekday()} 不在 {sorted(list(week))}")
                raise SkipTask("不在允许运行的星期范围内")
        else:
            if not runweek:
                self._line("未配置运行星期: 默认允许运行")
            else:
                try:
                    week = set(int(x) for x in runweek)
                except Exception:
                    week = set()
                week = {int(x) for x in week if 1 <= int(x) <= 7}
                if not week:
                    self._line("跳过: 未配置运行星期")
                    raise SkipTask("未配置运行星期")
                if now.isoweekday() not in week:
                    self._line(f"跳过: 星期 {now.isoweekday()} 不在 {sorted(list(week))}")
                    raise SkipTask("不在允许运行的星期范围内")

        self._set_stage("share_parse")
        self._section("解析分享链接")
        pwd_id, passcode, extracted_pdir_fid, _ = self.adapter.extract_url(self.task_data.get("shareurl") or "")
        if not pwd_id:
            raise bad_request("TASK_SHAREURL_INVALID", "无法解析分享链接")
        self._line(f"pwd_id: {pwd_id}")
        self._line(f"pdir_fid: {extracted_pdir_fid or ''}")

        self._set_stage("get_stoken")
        self._section("获取分享 token")
        def _validate_stoken(resp: dict[str, Any]) -> RetryResult:
            st = ((resp or {}).get("data") or {}).get("stoken")
            if st:
                return RetryResult(value=resp, ok=True)
            msg = (resp or {}).get("message") or "获取分享 token 失败"
            return RetryResult(value=resp, ok=False, error_message=f"{msg} resp={summarize_payload(resp)}")

        token_response = self._retry(action="get_stoken", fn=lambda: self.adapter.get_stoken(pwd_id, passcode or ""), validate=_validate_stoken)
        stoken = ((token_response or {}).get("data") or {}).get("stoken")
        if not stoken:
            message = (token_response or {}).get("message") or "获取分享 token 失败"
            raise RuntimeError(str(message))
        self._line("OK: stoken 获取成功")

        pdir_fid = str(extracted_pdir_fid or "")

        savepath = str(self.task_data.get("savepath") or "").rstrip("/")
        self._set_stage("ensure_dest_dir")
        self._section("准备保存目录")
        self._line(f"保存路径: {savepath}")
        dest_root_fid = self._ensure_dest_dir_fid(savepath)
        self._line(f"目标目录 fid: {dest_root_fid}")
        try:
            normalized_savepath = re.sub(r"/{2,}", "/", f"/{savepath}")
            if not hasattr(self.adapter, "savepath_fid") or not isinstance(getattr(self.adapter, "savepath_fid"), dict):
                setattr(self.adapter, "savepath_fid", {"/": "0"})
            self.adapter.savepath_fid[normalized_savepath] = str(dest_root_fid)
            if str(savepath).strip() and savepath != normalized_savepath:
                self.adapter.savepath_fid[str(savepath)] = str(dest_root_fid)
        except Exception:
            pass
        ignore_extension = bool(self.task_data.get("ignore_extension"))
        dest_listing = self._ls_dir(dest_root_fid)
        dest_file_list = (((dest_listing or {}).get("data") or {}).get("list")) or []
        dest_dir_map = {}
        dest_names = set()
        for raw in dest_file_list:
            name = _get_name(raw)
            if not name:
                continue
            if _is_dir(raw):
                fid = _get_fid(raw)
                if fid:
                    dest_dir_map[name] = fid
                continue
            dest_names.add(_normalize_name(name, ignore_extension))

        self._set_stage("fetch_share_items")
        self._section("读取分享列表")
        share_items = self._fetch_share_items(pwd_id=str(pwd_id), stoken=str(stoken), pdir_fid=pdir_fid)
        self._line(f"分享列表项数: {len(share_items)}")

        tree = Tree()
        tree.create_node(str(self.task_data.get("taskname") or ""), "root")

        root_files = [raw for raw in share_items if not _is_dir(raw)]
        addition = self.task_data.get("addition") or {}
        folder_mode = str(addition.get("folder_mode") or "normal").strip().lower()

        if folder_mode == "folder_diff":
            self._execute_folder_diff(
                share_items=share_items,
                root_files=root_files,
                pwd_id=str(pwd_id),
                stoken=str(stoken),
                dest_root_fid=str(dest_root_fid),
                dest_file_list=dest_file_list,
                dest_dir_map=dest_dir_map,
                savepath=savepath,
                ignore_extension=ignore_extension,
                tree=tree,
            )
        else:
            self._execute_normal(
                root_files=root_files,
                dest_file_list=dest_file_list,
                pwd_id=str(pwd_id),
                stoken=str(stoken),
                dest_root_fid=str(dest_root_fid),
                tree=tree,
            )

        if tree.size() <= 1:
            tree.create_node("无可转存文件", "empty", parent="root")
            self._line("无可转存文件")
        self._set_stage("end")
        return tree

    # ------------------------------------------------------------------
    # 普通模式：平铺转存根目录文件 + 重命名
    # ------------------------------------------------------------------
    def _execute_normal(self, *, root_files: list[dict], dest_file_list: list[dict], pwd_id: str, stoken: str, dest_root_fid: str, tree: Tree) -> None:
        self._set_stage("plan_transfer")
        self._section("生成转存计划")
        plan = self._plan_transfer(share_files=self._iter_files(root_files), dest_file_list=dest_file_list)
        self._line(f"待转存文件数: {len(plan)}")
        if not plan:
            return
        self._set_stage("save_file")
        self._section("执行转存")
        saved_fids = self._save_with_saved_fids(pwd_id=pwd_id, stoken=stoken, dest_root_fid=dest_root_fid, plan=plan)
        self._set_stage("rename")
        self._section("重命名")
        self._rename_by_fids(saved_fids=saved_fids, plan=plan, dest_root_fid=dest_root_fid)
        limit = min(len(plan), len(saved_fids))
        for i in range(limit):
            item = plan[i]
            saved_fid = str(saved_fids[i] or "").strip()
            data = {
                "fid": saved_fid,
                "file_name": item.origin_name,
                "file_name_re": item.target_name,
                "is_dir": False,
            }
            tree.create_node(f"{i + 1}. {item.origin_name} -> {item.target_name}", f"item-{i + 1}", parent="root", data=data)
        self._record_saved_latest([item.target_name for item in plan[:limit]])

    # ------------------------------------------------------------------
    # 文件夹对比模式：白名单文件夹按层级增量，其它文件夹整包转存一次
    # ------------------------------------------------------------------
    def _execute_folder_diff(
        self,
        *,
        share_items: list[dict],
        root_files: list[dict],
        pwd_id: str,
        stoken: str,
        dest_root_fid: str,
        dest_file_list: list[dict],
        dest_dir_map: dict[str, str],
        savepath: str,
        ignore_extension: bool,
        tree: Tree,
    ) -> None:
        addition = self.task_data.get("addition") or {}
        whitelist = [w.strip().lower() for w in str(addition.get("whitelist_folders") or "").split("|") if w.strip()]
        whitelist_is_any = str(addition.get("whitelist_mode") or "any").strip().lower() != "all"
        exclude_keywords = [w.strip().lower() for w in str(addition.get("folder_exclude") or "").split("|") if w.strip()]
        exclude_is_all = str(addition.get("folder_exclude_mode") or "any").strip().lower() == "all"

        self._line("模式: 文件夹对比（白名单增量 + 其它文件夹整包一次）")
        self._line(f"白名单: {'|'.join(whitelist) if whitelist else '(未设置，所有文件夹按增量处理)'}")
        transferred_names: list[str] = []

        # 1) 分享根目录下的文件：按普通模式（同名跳过，不改名）
        if root_files:
            existing_names: set[str] = set()
            for raw in dest_file_list:
                if _is_dir(raw):
                    continue
                name = _get_name(raw)
                if name:
                    existing_names.add(_normalize_name(name, ignore_extension))
            plan: list[DramaPlanItem] = []
            for raw in self._iter_files(root_files):
                name = _get_name(raw)
                fid = _get_fid(raw)
                token = _get_fid_token(raw)
                if not name or not fid:
                    continue
                if not token:
                    self._line(f"跳过缺少 fid_token 的文件: {name}")
                    continue
                if _normalize_name(name, ignore_extension) in existing_names:
                    continue
                plan.append(
                    DramaPlanItem(
                        fid=str(fid),
                        fid_token=str(token),
                        origin_name=name,
                        target_name=name,
                    )
                )
            if plan:
                self._set_stage("save_file")
                self._section("转存根目录文件")
                saved = self._save_with_saved_fids(pwd_id=pwd_id, stoken=stoken, dest_root_fid=dest_root_fid, plan=plan)
                limit = min(len(plan), len(saved))
                self._line(f"根目录新增文件: {limit}")
                for i in range(limit):
                    item = plan[i]
                    tree.create_node(f"{item.origin_name}", f"root-file-{i + 1}", parent="root", data={
                        "fid": str(saved[i] or ""),
                        "file_name": item.origin_name,
                        "file_name_re": item.target_name,
                        "is_dir": False,
                    })
                transferred_names.extend([item.origin_name for item in plan[:limit]])

        # 2) 子文件夹
        for raw in share_items:
            if not _is_dir(raw):
                continue
            name = _get_name(raw)
            fid = _get_fid(raw)
            if not name or not fid:
                continue
            lowered = name.lower()
            if exclude_keywords:
                if (all(kw in lowered for kw in exclude_keywords) if exclude_is_all else any(kw in lowered for kw in exclude_keywords)):
                    self._line(f"跳过排除文件夹: {name}")
                    continue
            if not whitelist or (any(kw in lowered for kw in whitelist) if whitelist_is_any else all(kw in lowered for kw in whitelist)):
                names = self._sync_share_dir_incremental(
                    pwd_id=pwd_id,
                    stoken=stoken,
                    share_fid=str(fid),
                    share_name=name,
                    dest_base_fid=dest_root_fid,
                    dest_base_path=savepath,
                    ignore_extension=ignore_extension,
                    tree=tree,
                    rel_label=name,
                    depth=0,
                    copy_all=False,
                )
                transferred_names.extend(names)
            else:
                names = self._save_share_dir_once(
                    pwd_id=pwd_id,
                    stoken=stoken,
                    share_fid=str(fid),
                    share_token=_get_fid_token(raw),
                    share_name=name,
                    dest_root_fid=dest_root_fid,
                    dest_dir_map=dest_dir_map,
                    savepath=savepath,
                    ignore_extension=ignore_extension,
                    tree=tree,
                )
                transferred_names.extend(names)
        self._record_saved_latest(transferred_names)

    def _sync_share_dir_incremental(
        self,
        *,
        pwd_id: str,
        stoken: str,
        share_fid: str,
        share_name: str,
        dest_base_fid: str,
        dest_base_path: str,
        ignore_extension: bool,
        tree: Tree,
        rel_label: str,
        depth: int,
        copy_all: bool,
    ) -> list[str]:
        if depth > 3:
            self._line(f"层级过深，跳过: {rel_label}")
            return []
        dest_path = f"{dest_base_path.rstrip('/')}/{share_name}" if share_name else dest_base_path
        try:
            dest_fid = self._ensure_dest_dir_fid(dest_path)
        except Exception as exc:
            self._line(f"创建目标目录失败 {dest_path}: {exc}")
            return []

        children = self._fetch_share_items(pwd_id=pwd_id, stoken=stoken, pdir_fid=share_fid)
        files = [item for item in children if not _is_dir(item)]
        dirs = [item for item in children if _is_dir(item)]
        existing_names = self._list_dest_names(dest_fid, ignore_extension) if not copy_all else set()

        plan: list[DramaPlanItem] = []
        for item in files:
            name = _get_name(item)
            fid = _get_fid(item)
            token = _get_fid_token(item)
            if not name or not fid:
                continue
            if not token:
                self._line(f"跳过缺少 fid_token 的文件: {name}")
                continue
            if not copy_all and _normalize_name(name, ignore_extension) in existing_names:
                continue
            plan.append(
                DramaPlanItem(
                    fid=str(fid),
                    fid_token=str(token),
                    origin_name=name,
                    target_name=name,
                )
            )
        transferred: list[str] = []
        if plan:
            self._line(f"增量目录 {dest_path}: {len(plan)} 个新文件")
            saved = self._save_with_saved_fids(pwd_id=pwd_id, stoken=stoken, dest_root_fid=dest_fid, plan=plan)
            limit = min(len(plan), len(saved))
            for i in range(limit):
                item = plan[i]
                tree.create_node(f"{item.origin_name}", f"dir-{rel_label}-{depth}-{i + 1}", parent="root", data={
                    "fid": str(saved[i] or ""),
                    "file_name": item.origin_name,
                    "file_name_re": item.target_name,
                    "is_dir": False,
                })
            transferred.extend([item.origin_name for item in plan[:limit]])

        for item in dirs:
            sub_name = _get_name(item)
            sub_fid = _get_fid(item)
            if not sub_name or not sub_fid:
                continue
            transferred.extend(
                self._sync_share_dir_incremental(
                    pwd_id=pwd_id,
                    stoken=stoken,
                    share_fid=str(sub_fid),
                    share_name=sub_name,
                    dest_base_fid=dest_fid,
                    dest_base_path=dest_path,
                    ignore_extension=ignore_extension,
                    tree=tree,
                    rel_label=f"{rel_label}/{sub_name}",
                    depth=depth + 1,
                    copy_all=copy_all,
                )
            )
        return transferred

    def _save_share_dir_once(
        self,
        *,
        pwd_id: str,
        stoken: str,
        share_fid: str,
        share_token: str | None,
        share_name: str,
        dest_root_fid: str,
        dest_dir_map: dict[str, str],
        savepath: str,
        ignore_extension: bool,
        tree: Tree,
    ) -> list[str]:
        if share_name in dest_dir_map:
            self._line(f"跳过已存在的文件夹: {share_name}")
            return []
        # 优先整包转存目录 fid
        if share_token:
            try:
                plan = [
                    DramaPlanItem(
                        fid=share_fid,
                        fid_token=str(share_token),
                        origin_name=share_name,
                        target_name=share_name,
                    )
                ]
                saved = self._save_with_saved_fids(pwd_id=pwd_id, stoken=stoken, dest_root_fid=dest_root_fid, plan=plan)
                if saved:
                    self._line(f"整包转存文件夹: {share_name}")
                    dest_dir_map[share_name] = str(saved[0] or "")
                    tree.create_node(f"📁{share_name}", f"folder-{share_name}", parent="root", data={"is_dir": True})
                    return []
            except Exception as exc:
                self._line(f"目录直转失败，改为逐文件转存: {exc}")
        # 退化：新建同名目录 + 递归转存全部文件
        self._line(f"逐文件整包转存文件夹: {share_name}")
        return self._sync_share_dir_incremental(
            pwd_id=pwd_id,
            stoken=stoken,
            share_fid=share_fid,
            share_name=share_name,
            dest_base_fid=dest_root_fid,
            dest_base_path=savepath,
            ignore_extension=ignore_extension,
            tree=tree,
            rel_label=share_name,
            depth=0,
            copy_all=True,
        )

    def _record_saved_latest(self, file_names: list[str]) -> None:
        """记录本次转存到的最大集数（写到任务的 extra.saved_latest）。"""
        if not file_names:
            return
        from datetime import datetime

        from app.services.episode_number import extract_episode, guess_episode

        extra = self.task_data.get("extra") if isinstance(self.task_data.get("extra"), dict) else {}
        prev = extra.get("saved_latest") if isinstance(extra.get("saved_latest"), dict) else {}
        try:
            prev_episode = int(prev.get("episode") or 0)
        except (TypeError, ValueError):
            prev_episode = 0

        mr = MagicRename(magic_regex=(self.task_data.get("magic_regex") if isinstance(self.task_data.get("magic_regex"), dict) else None))
        mr.set_taskname(str(self.task_data.get("taskname") or ""))
        pattern = str(self.task_data.get("pattern") or "")
        replace = str(self.task_data.get("replace") or "")
        if pattern and replace:
            try:
                pattern, replace = mr.magic_regex_conv(pattern, replace)
                mr._resolved_pattern = pattern
                mr._resolved_replace = replace
            except Exception:
                mr = None
        else:
            mr = None

        best_episode = prev_episode
        best_season = prev.get("season")
        best_name = str(prev.get("name") or "")
        for name in file_names:
            season, episode = extract_episode(name, mr=mr)
            if episode is None:
                episode = guess_episode(name)
            if episode is None:
                continue
            if int(episode) > int(best_episode or 0):
                best_episode = int(episode)
                best_season = season
                best_name = name
        if not best_episode:
            return
        self.saved_latest = {
            "episode": int(best_episode),
            "season": best_season,
            "name": best_name,
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
