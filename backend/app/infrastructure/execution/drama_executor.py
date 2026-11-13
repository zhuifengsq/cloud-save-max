from __future__ import annotations

import logging
import re
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable, TypeVar

from treelib import Tree
from app.infrastructure.execution.drama_plan import (DramaPlanItem, build_transfer_plan, _parse_size, _to_size, _normalize_name, _is_dir, _get_name, _get_fid, _get_fid_token, _get_updated_at)
from app.infrastructure.execution.drama_progress import calculate_saved_latest

from app.application.drama_schedule import (
    REASON_ALLOW_ONCE,
    REASON_NO_UPDATE_DAY,
    REASON_NO_RUNWEEK,
    REASON_OFF_DAY,
    requires_skip,
    resolve_run_schedule,
)
from app.domain.drive_types import DriveFile, TransferRequest
from app.infrastructure.drive.gateway import gateway_for_adapter
from app.core.errors import bad_request
from app.core.settings import settings
from app.infrastructure.execution.execution_log import ExecutionLog
from app.infrastructure.execution.magic_rename import MagicRename
from app.infrastructure.execution.retry_utils import RetryResult, retry_call, summarize_payload


logger = logging.getLogger(__name__)


class SkipTask(Exception):
    pass


T = TypeVar("T")






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
















class DramaTaskExecutor:
    def __init__(self, *, adapter: Any, task_data: dict[str, Any], log: ExecutionLog | None = None):
        self.adapter = adapter
        self.task_data = task_data
        self.log = log
        self.transfer_count = 0
        self.saved_latest: dict[str, Any] | None = None
        # 保存后用于统计集号的名字集合（目标已存在 ∪ 本次新增）
        self._saved_name_pool: list[str] = []
        # 拿不到文件清单时，用文件夹名里的 "N集" 作为兜底
        self._folder_episode_hints: list[int] = []

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

    def _add_saved_names(self, names: Iterable[str]) -> None:
        """把"目标目录里已存在的文件名"加入统计池（不产生额外请求）。"""
        for name in names:
            text = str(name or "").strip()
            if text:
                self._saved_name_pool.append(text)

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
            plan.append(
                DramaPlanItem(
                    fid=fid,
                    fid_token=str(token) if token is not None else None,
                    origin_name=str(name),
                    target_name=str(name),
                )
            )
        if not plan:
            return
        self._save_with_saved_fids(pwd_id=pwd_id, stoken=stoken, dest_root_fid=to_pdir_fid, plan=plan)

    def _save_with_saved_fids(self, *, pwd_id: str, stoken: str, dest_root_fid: str, plan: list[DramaPlanItem]) -> list[str]:
        def read_retry(action, fn):
            return self._retry(action=action, fn=fn,
                               validate=lambda result: _validate_code_status(action, result))

        gateway = gateway_for_adapter(self.adapter, read_retry=read_retry)
        saved = []
        for offset in range(0, len(plan), 100):
            batch = plan[offset:offset + 100]
            request = TransferRequest(str(pwd_id), str(stoken), str(dest_root_fid),
                tuple(DriveFile(item.fid, item.origin_name, token=item.fid_token or None) for item in batch))
            result = gateway.transfer_files(request)
            if result.state == "pending":
                raise SkipTask(result.message or "转存结果待确认")
            if result.state != "completed" or not result.confirmed:
                raise RuntimeError(result.message or "转存失败")
            saved.extend(item.id for item in result.files)
        self.transfer_count += len(saved)
        return saved

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

    def _file_filter_rules(self) -> tuple[int, list[str], list[str], bool]:
        addition = self.task_data.get("addition") or {}
        min_bytes = _parse_size(str(addition.get("min_size") or "")) or 0
        blocked = [word.strip().lower() for word in str(addition.get("filter_words") or "").split("|") if word.strip()]
        required = [word.strip().lower() for word in str(addition.get("file_filter") or "").split("|") if word.strip()]
        match_any = str(addition.get("file_filter_mode") or "").strip().lower() == "any"
        return min_bytes, blocked, required, match_any

    def _filter_transfer_files(self, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
        min_bytes, blocked, required, match_any = self._file_filter_rules()
        if min_bytes > 0:
            before = len(files)
            files = [file for file in files if (_to_size(file.get("size")) or 0) >= min_bytes]
            if before != len(files):
                min_size = (self.task_data.get("addition") or {}).get("min_size")
                self._line(f"大小过滤：移除 {before - len(files)} 个低于 {min_size} 的文件")

        if blocked:
            before = len(files)
            files = [file for file in files if not any(word in _get_name(file).lower() for word in blocked)]
            if before != len(files):
                self._line(f"关键词过滤：移除 {before - len(files)} 个匹配的文件")

        if required:
            matches = any if match_any else all
            before = len(files)
            files = [file for file in files if matches(word in _get_name(file).lower() for word in required)]
            if before != len(files):
                self._line(f"文件筛选：移除 {before - len(files)} 个不匹配的文件")
        return files

    def _plan_transfer(self, *, share_files: list[dict[str, Any]], dest_file_list: list[dict[str, Any]]) -> list[DramaPlanItem]:
        return build_transfer_plan(self.task_data, share_files=share_files,
                                   dest_file_list=dest_file_list, filter_files=self._filter_transfer_files)

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
        self._saved_name_pool.clear()
        self._folder_episode_hints.clear()
        now = datetime.now()
        self._set_stage("validate_schedule")
        self._section("验证调度条件")
        self._line(f"执行时间: {now.strftime('%Y-%m-%d %H:%M:%S')}")
        # 判定口径与「任务列表禁用执行按钮」「手动执行 409 拦截」共用
        # app.application.drama_schedule.resolve_run_schedule()，避免三处各写一套。
        decision = resolve_run_schedule(
            task_type="drama",
            extra=extra if isinstance(extra, dict) else {},
            now=now,
            tmdb_id=self.task_data.get("tmdb_id"),
            tmdb_media_type=self.task_data.get("tmdb_media_type"),
            tmdb_configured=bool(self.task_data.get("tmdb_configured")),
            episode_weekdays=self.task_data.get("tmdb_episode_weekdays") or [],
            update_weekdays=self.task_data.get("tmdb_update_weekdays") or [],
        )
        manual_run = bool(self.task_data.get("manual_run"))
        if decision.reason == REASON_ALLOW_ONCE:
            self._line("运行一次: 忽略按星期运行限制")
        elif decision.mode == "manual" and decision.reason == REASON_NO_RUNWEEK:
            self._line("未配置运行星期: 默认允许运行")
        elif decision.reason == REASON_NO_UPDATE_DAY:
            self._line("未识别到更新日，按每天执行")
        elif requires_skip(decision, manual_run):
            if decision.reason == REASON_OFF_DAY:
                self._line(f"跳过: 星期 {now.isoweekday()} 不在 {list(decision.weekdays)}")
            else:
                self._line(f"跳过: {decision.skip_reason}")
            raise SkipTask(decision.skip_reason)
        elif not decision.allowed:
            # 用户点「执行」= 现在就跑：运行星期只约束定时轮询，不拦手动执行。
            self._line(f"手动执行: 忽略运行日期限制（{decision.message}）")

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
        self._add_saved_names(_get_name(raw) for raw in dest_file_list if not _is_dir(raw))
        self._add_saved_names(item.target_name for item in plan[:limit])
        self._record_saved_latest()

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
            for raw in self._filter_transfer_files(self._iter_files(root_files)):
                name = _get_name(raw)
                fid = _get_fid(raw)
                token = _get_fid_token(raw)
                if not name or not fid:
                    continue
                if _normalize_name(name, ignore_extension) in existing_names:
                    continue
                plan.append(
                    DramaPlanItem(
                        fid=str(fid),
                        fid_token=str(token) if token is not None else None,
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
                self._add_saved_names(item.origin_name for item in plan[:limit])
            self._add_saved_names(_get_name(raw) for raw in dest_file_list if not _is_dir(raw))

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
                self._sync_share_dir_incremental(
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
            else:
                self._save_share_dir_once(
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
        self._record_saved_latest()

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
        self._add_saved_names(existing_names)

        plan: list[DramaPlanItem] = []
        for item in self._filter_transfer_files(files):
            name = _get_name(item)
            fid = _get_fid(item)
            token = _get_fid_token(item)
            if not name or not fid:
                continue
            if not copy_all and _normalize_name(name, ignore_extension) in existing_names:
                continue
            plan.append(
                DramaPlanItem(
                    fid=str(fid),
                    fid_token=str(token) if token is not None else None,
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
            self._add_saved_names(item.origin_name for item in plan[:limit])

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
            try:
                self._add_saved_names(self._list_dest_names(dest_dir_map[share_name], ignore_extension))
            except Exception as exc:
                self._line(f"统计已存在目录失败: {exc}")
            return []
        # 按目录 ID 直转无法检查其中的文件，存在文件规则时走逐文件路径。
        min_bytes, blocked, required, _ = self._file_filter_rules()
        if share_token and not (min_bytes > 0 or blocked or required):
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
                    # 整包直转没有文件清单，单独列一次目标目录统计集号
                    try:
                        saved_names = self._list_dest_names(str(saved[0] or ""), ignore_extension)
                    except Exception as exc:
                        saved_names = set()
                        self._line(f"统计目标目录失败: {exc}")
                    if saved_names:
                        self._add_saved_names(saved_names)
                    else:
                        from app.application.episode_number import episode_from_folder_name

                        hint = episode_from_folder_name(share_name)
                        if hint:
                            self._folder_episode_hints.append(int(hint))
                            self._line(f"按文件夹名记录集数: {hint}")
                    return []
            except SkipTask:
                # 审核中不是「目录直转失败」，不能降级成逐文件再刷一遍，直接上抛让任务记 skipped
                raise
            except Exception:
                # A failed write may have partially completed; do not resubmit its contents.
                raise
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

    def _clear_saved_latest(self) -> None:
        self.saved_latest = {
            "episode": 0,
            "season": None,
            "name": "",
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }

    def _record_saved_latest(self, file_names: list[str] | None = None) -> None:
        result = calculate_saved_latest(
            self.task_data, list(file_names) if file_names is not None else list(self._saved_name_pool),
            list(self._folder_episode_hints), at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        if result is not None:
            self.saved_latest = result
