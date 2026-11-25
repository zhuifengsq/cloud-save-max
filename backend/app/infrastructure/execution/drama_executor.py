from __future__ import annotations

import logging
import re
import json
import time
from hashlib import sha256
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable, NoReturn, TypeVar

from treelib import Tree
from app.application.drama_file_rules import FileFilters, split_words, transfer_target_key
from app.application.episode_number import episode_from_folder_name
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
from app.domain.drive_types import DriveFile, TransferRequest, TransferResult
from app.infrastructure.drive.gateway import gateway_for_adapter
from app.infrastructure.drive.transfer import QuarkTransferStrategy
from app.infrastructure.persistence.repositories.drama_transfers import (
    CHECKPOINT_RENAME_CHANGED,
    CHECKPOINT_SCOPE_CHANGED,
    CONFIGURATION_CHANGED,
    DramaTransferStore,
    TransferBatch,
    configuration_blocks_recovery,
)
from app.core.errors import bad_request
from app.core.settings import settings
from app.infrastructure.execution.execution_log import ExecutionLog
from app.domain.task_run import ExecutionLogPort
from app.infrastructure.execution.magic_rename import MagicRename
from app.infrastructure.execution.retry_utils import RetryResult, is_transient_error, retry_call, summarize_payload


logger = logging.getLogger(__name__)

FOLDER_SCOPE_RULES = {
    "whitelist_folders", "whitelist_folder_names", "whitelist_mode", "folder_exclude", "folder_exclude_mode",
}


class SkipTask(Exception):
    pass


class ShareSourceInvalidError(RuntimeError):
    """Confirmed invalid, expired, or removed share link."""


class ShareAccessError(RuntimeError):
    """Share access error that must not trigger share link replacement."""

    def __init__(self, message: str, *, reason: str = "unknown") -> None:
        super().__init__(message)
        self.reason = reason


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


def check_drama_schedule_skip(
    task_data: dict[str, Any],
    *,
    manual_run: bool,
    log: ExecutionLogPort | None = None,
) -> str | None:
    """Return skip_reason if scheduled drama task should skip before touching cloud drives."""
    extra = task_data.get("extra") or {}
    now = task_data.get("schedule_now") or datetime.now()
    decision = resolve_run_schedule(
        task_type="drama",
        extra=extra if isinstance(extra, dict) else {},
        now=now,
        tmdb_id=task_data.get("tmdb_id"),
        tmdb_media_type=task_data.get("tmdb_media_type"),
        tmdb_configured=bool(task_data.get("tmdb_configured")),
        episode_weekdays=task_data.get("tmdb_episode_weekdays") or [],
        update_weekdays=task_data.get("tmdb_update_weekdays") or [],
    )
    if not requires_skip(decision, manual_run):
        return None
    if log is not None:
        log.set_stage("validate_schedule")
        log.section("验证调度条件")
        log.line(f"执行时间: {now.strftime('%Y-%m-%d %H:%M:%S')}")
        if decision.reason == REASON_OFF_DAY:
            log.line(f"跳过: 星期 {now.isoweekday()} 不在 {list(decision.weekdays)}")
        else:
            log.line(f"跳过: {decision.skip_reason}")
    return decision.skip_reason


class DramaTaskExecutor:
    def __init__(self, *, adapter: Any, task_data: dict[str, Any], log: ExecutionLogPort | None = None):
        self.adapter = adapter
        self.task_data = task_data
        self.log = log
        self.transfer_count = 0
        self.rename_problem: tuple[str, str] | None = None
        self.saved_latest: dict[str, Any] | None = None
        # 保存后用于统计集号的名字集合（目标已存在 ∪ 本次新增）
        self._saved_name_pool: list[str] = []
        self.incomplete_directories: list[str] = []
        self.deferred_directories: list[str] = []
        self._fallback_dirs: set[str] = set()
        self._fallback_missing: dict[str, int] = {}
        self._fallback_scope: str | None = None
        self._fallback_excluded: set[str] = set()
        self._confirmed_batches: list[TransferBatch] = []
        self._transfer_scope = ""
        strategy = getattr(adapter, "TRANSFER_STRATEGY", None)
        self._quark_strategy = strategy if isinstance(strategy, QuarkTransferStrategy) else None
        self._transfer_store: DramaTransferStore | None = task_data.get("drama_transfer_store")
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
        requested_path = re.sub(r"/+", "/", dir_path).strip("/")
        matches: set[str] = set()
        pathless: set[str] = set()
        has_mismatch = False
        for item in fids:
            fid = item.get("fid")
            if not fid:
                continue
            returned_path = item.get("file_path") or item.get("path")
            if returned_path and re.sub(r"/+", "/", str(returned_path)).strip("/") != requested_path:
                has_mismatch = True
                continue
            if item.get("dir") is False or item.get("is_dir") is False:
                raise RuntimeError(f"目标目录路径指向文件: {dir_path}")
            (matches if returned_path else pathless).add(str(fid))
        if has_mismatch and not matches:
            raise RuntimeError(f"目标目录路径无法核实: {dir_path}")
        candidates = matches | pathless
        if len(candidates) > 1:
            raise RuntimeError(f"目标目录路径对应多个 ID: {dir_path}")
        return next(iter(candidates), None)

    def _ensure_dest_dir_fid(self, savepath: str) -> str:
        existing = self._try_get_dir_fid(savepath)
        if existing:
            return existing
        return self._create_dest_dir_fid(savepath)

    def _create_dest_dir_fid(self, savepath: str) -> str:
        try:
            response = self._mkdir(savepath)
        except RuntimeError:
            # The directory may have been created by a concurrent request.
            if created := self._try_get_dir_fid(savepath):
                return created
            raise
        data = response.get("data")
        if isinstance(data, dict) and data.get("fid"):
            return str(data["fid"])
        created = self._try_get_dir_fid(savepath)
        if created:
            return created
        raise RuntimeError("创建目录失败")

    def _supports_complete_listing(self) -> bool:
        return getattr(self.adapter, "SUPPORTS_COMPLETE_LISTING", None) is True

    def _list_dest_names(self, dest_fid: str, ignore_extension: bool) -> set[str]:
        if self._supports_complete_listing():
            listing = self._retry(
                action="ls_dir",
                fn=lambda: self.adapter.ls_dir_complete(dest_fid),
                validate=lambda r: _validate_code_status("ls_dir", r),
            )
        else:
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

    def _list_dest_for_diff(self, dest_fid: str, *, require_complete: bool = False) -> tuple[list[dict[str, Any]], bool]:
        if self._supports_complete_listing():
            listing = self.adapter.ls_dir_complete(dest_fid)
            if _validate_code_status("ls_dir", listing).ok:
                return (listing.get("data") or {}).get("list") or [], True
            raise SkipTask("无法完整读取目标目录，转存结果待确认")
        if require_complete:
            raise SkipTask("无法完整读取目标目录，转存结果待确认")
        listing = self._ls_dir(dest_fid)
        return (listing.get("data") or {}).get("list") or [], False

    @staticmethod
    def _dest_children(items: list[dict[str, Any]], ignore_extension: bool) -> tuple[set[str], dict[str, str], set[str]]:
        names: set[str] = set()
        directories: dict[str, str] = {}
        ambiguous: set[str] = set()
        for item in items:
            name = _get_name(item)
            if not name:
                continue
            if _is_dir(item):
                fid = _get_fid(item)
                if not fid or name in directories or name in ambiguous:
                    directories.pop(name, None)
                    ambiguous.add(name)
                else:
                    directories[name] = fid
            else:
                names.add(_normalize_name(name, ignore_extension))
        return names, directories, ambiguous

    def _add_saved_names(self, names: Iterable[str]) -> None:
        """把"目标目录里已存在的文件名"加入统计池（不产生额外请求）。"""
        for name in names:
            text = str(name or "").strip()
            if text:
                self._saved_name_pool.append(text)

    def _fetch_share_items(self, *, pwd_id: str, stoken: str, pdir_fid: str) -> tuple[list[dict[str, Any]], bool]:
        detail = self._fetch_share_detail(pwd_id=pwd_id, stoken=stoken, pdir_fid=pdir_fid)
        items = detail["data"]["list"]
        return items, self._share_listing_complete(detail, items)

    def _classify_share_response(self, response: Any, fallback_message: str) -> tuple[str, str]:
        if isinstance(response, dict):
            classifier = getattr(self.adapter, "classify_share_failure", None)
            if callable(classifier):
                classified = classifier(response)
                if isinstance(classified, dict):
                    kind = str(classified.get("kind") or "unknown").strip().lower()
                    msg = str(classified.get("message") or "").strip() or fallback_message
                    return kind, msg
            msg = str(response.get("message") or "").strip() or fallback_message
            return "unknown", msg
        return "unknown", fallback_message

    def _raise_classified_share_error(self, exc: Exception, response: Any, default_message: str) -> NoReturn:
        raw_message = str(exc).strip() or default_message
        kind, classified_msg = self._classify_share_response(response, raw_message)
        message = classified_msg or raw_message
        if kind == "invalid":
            raise ShareSourceInvalidError(message) from exc
        if kind == "pending":
            raise SkipTask(message) from exc
        if kind in {"auth", "throttled", "passcode", "captcha"}:
            raise ShareAccessError(message, reason=kind) from exc
        if is_transient_error(exc) or is_transient_error(message):
            raise ShareAccessError(message, reason="transient") from exc
        raise exc

    def _fetch_share_detail(self, *, pwd_id: str, stoken: str, pdir_fid: str) -> dict[str, Any]:
        last_detail_resp: list[Any] = [None]

        def _call_detail() -> dict[str, Any]:
            resp = self.adapter.get_detail(pwd_id, stoken, pdir_fid or "")
            last_detail_resp[0] = resp
            return resp

        def _validate(detail: dict[str, Any]) -> RetryResult:
            if not isinstance(detail, dict):
                return RetryResult(value=detail, ok=False, error_message=f"get_detail invalid response: {summarize_payload(detail)}")
            status = detail.get("status")
            if status is not None and status not in (200, "200"):
                msg = f"get_detail status={status} message={detail.get('message') or ''} resp={summarize_payload(detail)}"
                kind, _ = self._classify_share_response(detail, msg)
                return RetryResult(value=detail, ok=False, error_message=msg, retryable=kind not in {"invalid", "auth", "passcode", "captcha", "pending"})
            code = detail.get("code")
            if code is not None and code not in (0, "0"):
                msg = f"get_detail code={code} message={detail.get('message') or ''} resp={summarize_payload(detail)}"
                kind, _ = self._classify_share_response(detail, msg)
                return RetryResult(value=detail, ok=False, error_message=msg, retryable=kind not in {"invalid", "auth", "passcode", "captcha", "pending"})
            data = detail.get("data")
            if not isinstance(data, dict) or not isinstance(data.get("list"), list):
                return RetryResult(value=detail, ok=False, error_message=f"get_detail invalid list: {summarize_payload(detail)}")
            if any(not isinstance(item, dict) for item in data["list"]):
                return RetryResult(value=detail, ok=False, error_message=f"get_detail invalid item: {summarize_payload(detail)}")
            return RetryResult(value=detail, ok=True)

        try:
            return self._retry(action="get_detail", fn=_call_detail, validate=_validate)
        except Exception as exc:
            self._raise_classified_share_error(exc, last_detail_resp[0], "获取分享详情失败")

    def _share_listing_complete(self, detail: dict[str, Any], items: list[dict[str, Any]]) -> bool:
        metadata = detail.get("metadata", {})
        if not isinstance(metadata, dict) or metadata.get("_listing_complete", True) is not True:
            return False
        if "_total" in metadata:
            if str(metadata["_total"]) != str(len(items)):
                return False
        elif getattr(self.adapter, "DRIVE_TYPE", "") in ("quark", "uc"):
            # These adapters request the server total to drive pagination.
            return False
        # Other adapters finish their pagination internally and may omit totals.
        fids = {_get_fid(item) for item in items if _get_name(item)}
        return "" not in fids and len(fids) == len(items)

    def _transfer_read(self, action, fn):
        started = time.monotonic()
        try:
            if self._quark_strategy is not None and action == "query_task":
                # The adapter owns the poll budget. Preserve its error envelope.
                return fn()
            return self._retry(action=action, fn=fn, validate=lambda result: _validate_code_status(action, result))
        finally:
            elapsed = time.monotonic() - started
            if self._quark_strategy is not None and elapsed >= 1:
                self._line(f"{action} 耗时: {elapsed:.2f}s")

    def _save_with_saved_fids(self, *, pwd_id: str, stoken: str, dest_root_fid: str, plan: list[DramaPlanItem],
                              is_dir: bool = False, defer_rename: bool = False) -> list[str]:
        gateway = gateway_for_adapter(self.adapter, read_retry=self._transfer_read)
        rename_signature = self._rename_signature() if defer_rename else ""
        saved = []
        for offset in range(0, len(plan), 100):
            self._set_stage('save_file')
            batch = plan[offset:offset + 100]
            request = TransferRequest(str(pwd_id), str(stoken), str(dest_root_fid),
                tuple(DriveFile(item.fid, item.origin_name, token=item.fid_token or None) for item in batch))
            if self._transfer_store is None or self._quark_strategy is None:
                result = gateway.transfer_files(request)
                fids = self._confirmed_fids(result)
                if defer_rename:
                    checkpoint = TransferBatch(dest_root_fid, [
                        (item.fid, saved_file.name,
                         saved_file.name if item.origin_name == item.target_name else item.target_name)
                        for item, saved_file in zip(batch, result.files, strict=True)
                    ], defer_rename=True, saved_fids=[f.stable_id or f.id for f in result.files],
                        rename_signature=rename_signature)
                    if self._transfer_store is not None:
                        self._transfer_store.confirm_normal_batch(self._transfer_scope, checkpoint)
                    self._confirmed_batches.append(checkpoint)
            else:
                checkpoint = TransferBatch(dest_root_fid, [
                    (item.fid, item.origin_name, item.target_name) for item in batch
                ], is_dir=is_dir, defer_rename=defer_rename, rename_signature=rename_signature)
                self._transfer_store.begin_batch(self._transfer_scope, checkpoint)
                def remember_job(job: str) -> None:
                    checkpoint.task_id = job
                    self._transfer_store.update_batch(self._transfer_scope, checkpoint)

                result = self._quark_strategy.transfer(
                    self.adapter, request, self._transfer_read, on_task=remember_job,
                    on_output=lambda ids: self._record_transfer_output(checkpoint, ids),
                    on_submitted=self._log_transfer_submission,
                )
                fids = self._finish_tracked_batch(checkpoint, result)
            saved.extend(fids)
            self.transfer_count += len(fids)
        return saved

    def _log_transfer_submission(self, elapsed: float, outcome: str) -> None:
        if elapsed >= 1 or outcome != "已响应":
            self._line(f"save_file 耗时: {elapsed:.2f}s（{outcome}）")

    def _record_transfer_output(self, checkpoint: TransferBatch, ids: list[str]) -> None:
        store = self._transfer_store
        assert store is not None
        checkpoint.output_fids = ids
        store.update_batch(self._transfer_scope, checkpoint)

    @staticmethod
    def _confirmed_fids(result: TransferResult) -> list[str]:
        if result.state == "pending":
            raise SkipTask(result.message or "转存结果待确认")
        if result.state != "completed" or not result.confirmed:
            raise RuntimeError(result.message or "转存失败")
        return [item.id for item in result.files]

    def _finish_tracked_batch(self, checkpoint: TransferBatch, result: TransferResult) -> list[str]:
        store = self._transfer_store
        assert store is not None
        if result.state == "failed":
            store.update_batch(self._transfer_scope, None)
        elif result.state in ("pending", "query_error"):
            missing_id = not (checkpoint.task_id or checkpoint.output_fids)
            reason = ("转存结果待确认，未取得夸克任务 ID" if missing_id
                      else result.message or "转存结果待确认")
            message = store.record_problem(self._transfer_scope, reason, manual=missing_id)
            if result.state == "query_error":
                raise RuntimeError(message)
            raise SkipTask(message)
        saved_fids = self._confirmed_fids(result)
        if checkpoint.defer_rename:
            checkpoint.saved_fids = saved_fids
            store.confirm_normal_batch(self._transfer_scope, checkpoint)
            self._confirmed_batches.append(checkpoint)
        else:
            store.update_batch(self._transfer_scope, None)
        self._line(f"已确认转存批次: {len(saved_fids)} 项")
        return saved_fids

    def _finish_normal_renames(self, tree: Tree, *, verify_first: bool = False) -> None:
        store = self._transfer_store
        if not self._confirmed_batches:
            assert store is not None
            raise SkipTask(store.record_problem(
                self._transfer_scope, "重命名断点没有已确认的转存批次", manual=True
            ))
        plan = [
            DramaPlanItem(fid, None, origin, target)
            for batch in self._confirmed_batches
            for fid, origin, target in batch.files
        ]
        fids = [fid for batch in self._confirmed_batches for fid in (batch.saved_fids or [])]
        self._set_stage("rename")
        self._section("重命名")
        if store is not None and not verify_first:
            store.start_renames(self._transfer_scope)
        verified, observed = self._rename_by_fids(saved_fids=fids, plan=plan,
                                        dest_root_fid=self._confirmed_batches[0].destination, verify_first=verify_first)
        if verified is not True:
            reason = "转存已确认，重命名结果待确认" if verified is None else "转存已确认，部分文件重命名失败"
            if store is not None:
                reason = store.record_problem(self._transfer_scope, reason)
            if self._quark_strategy is not None:
                raise (SkipTask if verified is None else RuntimeError)(reason)
            self.rename_problem = ("skipped" if verified is None else "failed", reason)
        elif store is not None:
            store.verify_renames(self._transfer_scope)
        self._add_normal_results(tree, plan, fids, observed)
        renamed_from = {
            item.target_name: item.origin_name for item, fid in zip(plan, fids)
            if item.origin_name != item.target_name and observed.get(fid) == item.target_name
        }
        self._record_saved_latest(renamed_from=renamed_from)

    def _rename_signature(self) -> str:
        rules = self.task_data.get("magic_regex")
        rename = MagicRename(magic_regex=rules if isinstance(rules, dict) else None)
        pattern, replace = rename.magic_regex_conv(str(self.task_data.get("pattern") or ""),
                                                   str(self.task_data.get("replace") or ""))
        effective_replace = replace.replace("{TASKNAME}", str(self.task_data.get("taskname") or ""))
        return sha256(json.dumps([pattern, effective_replace]).encode()).hexdigest()

    def _resume_transfer(self, tree: Tree) -> bool:
        store = self._transfer_store
        if store is None:
            return False
        identity = [
            self.task_data.get("task_uid"),
            str(getattr(self.adapter, "DRIVE_TYPE", "") or ""),
            str(getattr(self.adapter, "account_name", "") or ""),
            self.task_data.get("shareurl"),
            self.task_data.get("savepath"),
        ]
        addition = self.task_data.get("addition")
        naming = [
            self.task_data.get("pattern"),
            self.task_data.get("replace"),
            self.task_data.get("ignore_extension"),
        ]
        scope = [*identity, addition, *naming]
        self._transfer_scope = sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()
        self._fallback_scope = None
        if isinstance(addition, dict) and str(addition.get("folder_mode") or "").strip().lower() == "folder_diff":
            unchanged_rules = {key: value for key, value in addition.items() if key not in FOLDER_SCOPE_RULES}
            self._fallback_scope = sha256(json.dumps([*identity, unchanged_rules, *naming], sort_keys=True).encode()).hexdigest()
        row = store.load()
        if row is None:
            return False
        self._set_stage("save_file")
        configuration_blocked = configuration_blocks_recovery(row.recovery)
        if row.recovery.get("blocked") and not configuration_blocked:
            raise SkipTask(f"需人工确认：{row.recovery.get('reason') or '转存结果待确认'}")
        mismatch = None
        if row.scope != self._transfer_scope:
            if self._fallback_scope and store.adopt_fallback_scope(
                row.scope, self._transfer_scope, self._fallback_scope
            ):
                configuration_blocked = False
                self._line("目录选择规则已变更，按当前范围继续处理旧目录断点")
            else:
                mismatch = CHECKPOINT_SCOPE_CHANGED
        else:
            signatures = {batch.get("rename_signature") for batch in [*row.confirmed, row.batch or {}]} - {None, ""}
            if signatures and signatures != {self._rename_signature()}:
                mismatch = CHECKPOINT_RENAME_CHANGED
        if mismatch:
            if configuration_blocked and row.recovery.get("reason") == mismatch:
                raise SkipTask(f"需人工确认：{mismatch}")
            raise SkipTask(store.record_problem(row.scope, mismatch, manual=True, kind=CONFIGURATION_CHANGED))
        if configuration_blocked:
            store.clear_configuration_problem(row.scope)
            self._line("转存配置已恢复匹配，继续处理旧断点")
        if self._fallback_scope and row.fallback_dirs and row.fallback_scope is None:
            store.remember_fallback_scope(self._transfer_scope, self._fallback_scope)
        self._fallback_dirs.update(row.fallback_dirs)
        self._fallback_missing.update(row.fallback_missing)
        self._fallback_excluded.update(row.fallback_excluded)
        self._confirmed_batches.extend(TransferBatch(**batch) for batch in row.confirmed)
        self.transfer_count += sum(len(batch.saved_fids or []) for batch in self._confirmed_batches)
        if row.renames_started:
            self._finish_normal_renames(tree, verify_first=True)
            return True
        if row.batch is None:
            return False
        if self._quark_strategy is None:
            raise SkipTask(store.record_problem(row.scope, "原夸克转存仍待确认，当前账号无法续查", manual=True))
        checkpoint = TransferBatch(**row.batch)
        if not (checkpoint.task_id or checkpoint.output_fids):
            raise SkipTask(store.record_problem(row.scope, "转存结果待确认，未取得夸克任务 ID", manual=True))
        request = TransferRequest("", "", checkpoint.destination, tuple(
            DriveFile(fid, origin) for fid, origin, _target in checkpoint.files
        ))
        if checkpoint.output_fids:
            self._line("按已保存的文件 ID 继续确认夸克转存批次")
            result = self._quark_strategy.confirm_files(
                self.adapter, request, self._transfer_read, checkpoint.task_id,
                checkpoint.destination, checkpoint.output_fids,
            )
        else:
            self._line(f"继续确认夸克转存批次: {checkpoint.task_id}")
            result = self._quark_strategy.confirm_task(
                self.adapter, request, self._transfer_read, checkpoint.task_id, checkpoint.destination,
                on_output=lambda ids: self._record_transfer_output(checkpoint, ids),
            )
        saved = self._finish_tracked_batch(checkpoint, result)
        self.transfer_count += len(saved)
        if checkpoint.defer_rename:
            return False
        for index, (fid, (_, origin, target)) in enumerate(zip(saved, checkpoint.files, strict=True)):
            tree.create_node(target, f"resumed-{index}", parent="root", data={
                "fid": fid, "file_name": origin, "file_name_re": target, "is_dir": checkpoint.is_dir,
            })
            if not checkpoint.is_dir:
                self._add_saved_names([target])
        return False

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

    def _file_filter_rules(self) -> FileFilters:
        addition = self.task_data.get("addition") or {}
        return FileFilters(
            min_bytes=_parse_size(str(addition.get("min_size") or "")) or 0,
            blocked=split_words(addition.get("filter_words")),
            required=split_words(addition.get("file_filter")),
            match_any=str(addition.get("file_filter_mode") or "").strip().lower() == "any",
            min_date=str(addition.get("file_min_date") or "").strip(),
        )

    def _filter_transfer_files(self, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rules = self._file_filter_rules()
        counts: dict[str, int] = {}
        selected = []
        for file in files:
            reason = rules.rejection(_get_name(file), _to_size(file.get("size")), _get_updated_at(file))
            if reason is None:
                selected.append(file)
            else:
                counts[reason] = counts.get(reason, 0) + 1
        min_size = (self.task_data.get("addition") or {}).get("min_size")
        if count := counts.get("size"):
            self._line(f"大小过滤：移除 {count} 个低于 {min_size} 的文件")
        if count := counts.get("keyword"):
            self._line(f"关键词过滤：移除 {count} 个匹配的文件")
        if count := counts.get("file_filter"):
            self._line(f"文件筛选：移除 {count} 个不匹配的文件")
        if count := counts.get("file_date"):
            self._line(f"时间过滤：移除 {count} 个早于 {rules.min_date} 的文件")
        return selected

    def _plan_transfer(self, *, share_files: list[dict[str, Any]], dest_file_list: list[dict[str, Any]]) -> list[DramaPlanItem]:
        return build_transfer_plan(self.task_data, share_files=share_files,
                                   dest_file_list=dest_file_list, filter_files=self._filter_transfer_files)

    def _rename_one(self, *, fid: str, origin: str, target: str, destination: str) -> bool:
        fid = str(fid or "").strip()
        origin = str(origin or "")
        target = str(target or "")
        if not fid or not origin or not target or origin == target:
            return True
        target_fid = (
            f"{destination.rstrip('/')}/{origin}"
            if self._quark_strategy is None and getattr(self.adapter, "DRIVE_TYPE", None) == "baidu"
            else fid
        )
        try:
            self._retry(
                action="rename",
                fn=lambda: self.adapter.rename(target_fid, target),
                validate=lambda r: _validate_code_status("rename", r),
            )
            return True
        except Exception as exc:
            self._line(f"FAIL: 重命名失败 {origin} → {target} err={str(exc).strip() or type(exc).__name__}")
            return False

    def _rename_by_fids(self, *, saved_fids: list[str], plan: list[DramaPlanItem], dest_root_fid: str,
                        verify_first: bool = False) -> tuple[bool | None, dict[str, str]]:
        attempted = [
            (str(fid), item.origin_name, item.target_name)
            for fid, item in zip(saved_fids, plan)
            if fid and item.origin_name and item.target_name and item.origin_name != item.target_name
        ]
        if not attempted:
            return True, {}

        if not verify_first:
            rename_batch = getattr(self.adapter, "rename_batch", None)
            if (
                len(attempted) > 1
                and self._quark_strategy is None
                and getattr(self.adapter, "DRIVE_TYPE", None) == "baidu"
                and callable(rename_batch)
            ):
                pairs = [(f"{dest_root_fid.rstrip('/')}/{origin}", target) for _fid, origin, target in attempted]
                try:
                    self._retry(
                        action="rename_batch",
                        fn=lambda: rename_batch(pairs),
                        validate=lambda r: _validate_code_status("rename_batch", r),
                    )
                    self._line(f"批量重命名: {len(pairs)} 项")
                except Exception as exc:
                    self._line(f"批量重命名失败，回退逐条重命名: {str(exc).strip() or type(exc).__name__}")
                    for fid, origin, target in attempted:
                        self._line(f"重命名：{origin} → {target}")
                        self._rename_one(fid=fid, origin=origin, target=target, destination=dest_root_fid)
            else:
                for fid, origin, target in attempted:
                    self._line(f"重命名：{origin} → {target}")
                    self._rename_one(fid=fid, origin=origin, target=target, destination=dest_root_fid)

        fid_name = self._read_rename_names(dest_root_fid)
        if fid_name is None:
            return None, {}
        retry = [
            (fid, origin, target) for fid, origin, target in attempted
            if fid in fid_name and fid_name[fid] != target
            and (not verify_first or fid_name[fid] == origin)
            and (verify_first or self._quark_strategy is not None)
        ]
        if retry:
            self._line(f"重命名校验: {len(retry)} 项名称不符")
            for fid, origin, target in retry:
                self._rename_one(fid=fid, origin=origin, target=target, destination=dest_root_fid)
            after_retry = self._read_rename_names(dest_root_fid)
            if after_retry is None:
                attempted_fids = {fid for fid, _, _ in retry}
                return None, {fid: name for fid, name in fid_name.items() if fid not in attempted_fids}
            fid_name = after_retry

        unknown = sum(fid not in fid_name for fid, _, _ in attempted)
        ok = sum(fid_name.get(fid) == target for fid, _, target in attempted)
        failed = len(attempted) - ok - unknown
        self._line(f"重命名摘要: total={len(attempted)} ok={ok} failed={failed} unknown={unknown}")
        if failed:
            return False, fid_name
        return (None if unknown else True), fid_name

    def _read_rename_names(self, dest_fid: str) -> dict[str, str] | None:
        try:
            if self._supports_complete_listing():
                listing = self._retry(action="ls_dir", fn=lambda: self.adapter.ls_dir_complete(dest_fid),
                                      validate=lambda r: _validate_code_status("ls_dir", r))
            else:
                listing = self._ls_dir(dest_fid)
        except Exception as exc:
            self._line(f"重命名校验未知: 无法读取目标目录 err={str(exc).strip() or type(exc).__name__}")
            return None
        items = (listing.get("data") or {}).get("list") or []
        return {self._saved_identity(raw): _get_name(raw) for raw in items if not _is_dir(raw)}

    def _saved_identity(self, raw: dict) -> str:
        if getattr(self.adapter, "DRIVE_TYPE", None) == "baidu":
            # Path alone cannot identify a file after another file takes its place.
            return str(raw.get("fs_id") or raw.get("share_fid_token") or "")
        return _get_fid(raw)

    def execute(self) -> Tree:
        extra = self.task_data.get("extra") or {}
        self._saved_name_pool.clear()
        self._folder_episode_hints.clear()
        self.incomplete_directories.clear()
        self.deferred_directories.clear()
        self._fallback_missing.clear()
        self._fallback_excluded.clear()
        self._confirmed_batches.clear()
        self.transfer_count = 0
        self.rename_problem = None
        now = self.task_data.get("schedule_now") or datetime.now()
        self._set_stage("validate_schedule")
        self._section("验证调度条件")
        self._line(f"执行时间: {now.strftime('%Y-%m-%d %H:%M:%S')}")
        # 与任务列表和详情共用同一套运行日期判定。
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

        tree = Tree()
        tree.create_node(str(self.task_data.get("taskname") or ""), "root")
        self._fallback_dirs.clear()
        if self._resume_transfer(tree):
            self._set_stage("end")
            return tree

        self._set_stage("share_parse")
        self._section("解析分享链接")
        pwd_id, passcode, extracted_pdir_fid, _ = self.adapter.extract_url(self.task_data.get("shareurl") or "")
        if not pwd_id:
            raise bad_request("TASK_SHAREURL_INVALID", "无法解析分享链接")
        self._line(f"pwd_id: {pwd_id}")
        self._line(f"pdir_fid: {extracted_pdir_fid or ''}")

        self._set_stage("get_stoken")
        self._section("获取分享 token")
        last_token_resp: list[Any] = [None]

        def _call_get_stoken() -> dict[str, Any]:
            resp = self.adapter.get_stoken(pwd_id, passcode or "")
            last_token_resp[0] = resp
            return resp

        def _validate_stoken(resp: dict[str, Any]) -> RetryResult:
            st = ((resp or {}).get("data") or {}).get("stoken")
            if st:
                return RetryResult(value=resp, ok=True)
            msg = (resp or {}).get("message") or "获取分享 token 失败"
            kind, _ = self._classify_share_response(resp, str(msg))
            return RetryResult(
                value=resp,
                ok=False,
                error_message=f"{msg} resp={summarize_payload(resp)}",
                retryable=kind not in {"invalid", "auth", "passcode", "captcha", "pending"},
            )

        try:
            token_response = self._retry(action="get_stoken", fn=_call_get_stoken, validate=_validate_stoken)
        except Exception as exc:
            self._raise_classified_share_error(exc, last_token_resp[0], "获取分享 token 失败")
        stoken = ((token_response or {}).get("data") or {}).get("stoken")
        if not stoken:
            message = (token_response or {}).get("message") or "获取分享 token 失败"
            self._raise_classified_share_error(RuntimeError(str(message)), token_response, "获取分享 token 失败")
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
        addition = self.task_data.get("addition") or {}
        folder_mode = str(addition.get("folder_mode") or "normal").strip().lower()
        supports_complete = self._supports_complete_listing()
        tracked_quark = self._quark_strategy is not None and self._transfer_store is not None
        if folder_mode == "folder_diff":
            dest_file_list, dest_complete = self._list_dest_for_diff(
                dest_root_fid, require_complete=supports_complete or tracked_quark
            )
            dest_names, dest_dir_map, ambiguous_dirs = self._dest_children(dest_file_list, ignore_extension)
        elif supports_complete or tracked_quark:
            dest_file_list, _ = self._list_dest_for_diff(dest_root_fid, require_complete=True)
        else:
            dest_listing = self._ls_dir(dest_root_fid)
            dest_file_list = (dest_listing.get("data") or {}).get("list") or []

        self._set_stage("fetch_share_items")
        self._section("读取分享列表")
        share_items, share_complete = self._fetch_share_items(
            pwd_id=str(pwd_id), stoken=str(stoken), pdir_fid=pdir_fid,
        )
        self._line(f"分享列表项数: {len(share_items)}")
        if not share_complete:
            self._line("分享目录列表不完整: /")
            self.incomplete_directories.append("/")

        root_files = [raw for raw in share_items if not _is_dir(raw)] if share_complete else []

        if folder_mode == "folder_diff":
            self._execute_folder_diff(
                share_items=share_items,
                root_files=root_files,
                pwd_id=str(pwd_id),
                stoken=str(stoken),
                dest_root_fid=str(dest_root_fid),
                dest_file_list=dest_file_list,
                dest_dir_map=dest_dir_map,
                dest_names=dest_names,
                dest_complete=dest_complete,
                ambiguous_dirs=ambiguous_dirs,
                share_complete=share_complete,
                savepath=savepath,
                ignore_extension=ignore_extension,
                tree=tree,
            )
        elif share_complete:
            self._execute_normal(
                root_files=root_files,
                dest_file_list=dest_file_list,
                pwd_id=str(pwd_id),
                stoken=str(stoken),
                dest_root_fid=str(dest_root_fid),
                tree=tree,
            )

        if tree.size() <= 1 and not self.incomplete_directories:
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
        if any(batch.destination != dest_root_fid for batch in self._confirmed_batches):
            assert self._transfer_store is not None
            raise SkipTask(self._transfer_store.record_problem(
                self._transfer_scope, "保存目录已变更，原转存批次仍待确认", manual=True
            ))
        confirmed_fids = {fid for batch in self._confirmed_batches for fid in (batch.saved_fids or [])}
        confirmed_sources = {fid for batch in self._confirmed_batches for fid, _, _ in batch.files}
        ignore_extension = bool(self.task_data.get("ignore_extension"))
        confirmed_targets = {
            transfer_target_key(target, ignore_extension)
            for batch in self._confirmed_batches for _, _, target in batch.files
        }
        # Plan against the directory as seen before this run's batches. Their
        # sources still take part in conflict resolution, but are never re-sent.
        if not confirmed_fids.issubset({self._saved_identity(raw) for raw in dest_file_list}):
            assert self._transfer_store is not None
            raise SkipTask(self._transfer_store.record_problem(
                self._transfer_scope, "已确认转存的文件不在目标目录，转存结果待确认"
            ))
        previous_dest = [raw for raw in dest_file_list if self._saved_identity(raw) not in confirmed_fids]
        plan = self._plan_transfer(share_files=self._iter_files(root_files), dest_file_list=previous_dest)
        plan = [
            item for item in plan
            if item.fid not in confirmed_sources and transfer_target_key(item.target_name, ignore_extension) not in confirmed_targets
        ]
        self._line(f"待转存文件数: {len(plan)}")
        self._add_saved_names(_get_name(raw) for raw in previous_dest if not _is_dir(raw))
        if plan:
            self._section("执行转存")
            self._save_with_saved_fids(pwd_id=pwd_id, stoken=stoken, dest_root_fid=dest_root_fid,
                                       plan=plan, defer_rename=True)
        if self._confirmed_batches:
            self._finish_normal_renames(tree)
        else:
            self._record_saved_latest()

    def _add_normal_results(self, tree: Tree, plan: list[DramaPlanItem], saved_fids: list[str], observed: dict[str, str]) -> None:
        limit = min(len(plan), len(saved_fids))
        for i in range(limit):
            item = plan[i]
            saved_fid = str(saved_fids[i] or "").strip()
            name = item.origin_name if item.origin_name == item.target_name else observed.get(saved_fid)
            data = {
                "fid": saved_fid,
                "file_name": item.origin_name,
                "file_name_re": name,
                "is_dir": False,
            }
            label = f"{item.origin_name} -> {name}" if name else f"{item.origin_name}（已转存，当前名称待确认）"
            tree.create_node(f"{i + 1}. {label}", f"item-{i + 1}", parent="root", data=data)
            if name:
                self._add_saved_names([name])

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
        dest_names: set[str] | None = None,
        dest_complete: bool = False,
        ambiguous_dirs: set[str] | None = None,
        share_complete: bool = False,
    ) -> None:
        ambiguous_dirs = ambiguous_dirs or set()
        share_names = {_get_name(item) for item in share_items if _is_dir(item) and _get_fid(item)}
        addition = self.task_data.get("addition") or {}
        whitelist = [w.strip().lower() for w in str(addition.get("whitelist_folders") or "").split("|") if w.strip()]
        selected_names = addition.get("whitelist_folder_names")
        selected_folders = (
            {name for name in selected_names if isinstance(name, str) and name}
            if isinstance(selected_names, list)
            else set()
        )
        whitelist_match = all if str(addition.get("whitelist_mode") or "any").strip().lower() == "all" else any
        exclude_keywords = [w.strip().lower() for w in str(addition.get("folder_exclude") or "").split("|") if w.strip()]
        matches_exclude = all if str(addition.get("folder_exclude_mode") or "any").strip().lower() == "all" else any

        def is_excluded(name: str) -> bool:
            lowered_name = name.lower()
            return bool(exclude_keywords) and matches_exclude(kw in lowered_name for kw in exclude_keywords)

        excluded = {name for name in self._fallback_dirs if is_excluded(name)}
        paused_dirs = self._check_fallback_directories(share_names, share_complete, excluded)

        self._line("模式: 文件夹对比（白名单增量 + 其它文件夹整包一次）")
        whitelist_labels = [*whitelist, *sorted(selected_folders)]
        self._line(f"白名单: {' | '.join(whitelist_labels) if whitelist_labels else '(未设置，所有文件夹按增量处理)'}")

        # 1) 分享根目录下的文件：按普通模式（同名跳过，不改名）
        self._add_saved_names(_get_name(raw) for raw in dest_file_list if not _is_dir(raw))
        if root_files:
            existing_names = dest_names if dest_names is not None else self._dest_children(dest_file_list, ignore_extension)[0]
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

        # 2) 子文件夹
        for raw in share_items:
            if not _is_dir(raw):
                continue
            name = _get_name(raw)
            fid = _get_fid(raw)
            if not name or not fid:
                continue
            if name in paused_dirs:
                continue
            lowered = name.lower()
            if is_excluded(name):
                self._line(f"跳过排除文件夹: {name}")
                continue
            if name in ambiguous_dirs:
                self._mark_ambiguous_directory(name)
                continue
            matches_keywords = bool(whitelist) and whitelist_match(kw in lowered for kw in whitelist)
            incremental = (not whitelist and not selected_folders) or name in selected_folders or matches_keywords
            if incremental and name not in self._fallback_dirs:
                self._sync_share_dir_incremental(
                    pwd_id=pwd_id,
                    stoken=stoken,
                    share_fid=str(fid),
                    share_name=name,
                    dest_base_path=savepath,
                    known_dest_fid=dest_dir_map.get(name),
                    known_missing=dest_complete and name not in dest_dir_map,
                    ignore_extension=ignore_extension,
                    tree=tree,
                    rel_label=name,
                    depth=0,
                    copy_all=False,
                    verify_destination=self._quark_strategy is not None and self._transfer_store is not None,
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
                    dest_complete=dest_complete,
                )
        for directory in sorted(self._fallback_dirs - paused_dirs):
            if not any(path == directory or path.startswith(directory + "/") for path in self.incomplete_directories):
                if directory in share_names:
                    self._line(f"待续跑的分享目录本轮未处理: {directory}")
                else:
                    self._line(f"未找到待继续转存的分享目录: {directory}")
                self.incomplete_directories.append(directory)
        self._record_saved_latest()

    def _check_fallback_directories(self, share_names: set[str], share_complete: bool, excluded: set[str]) -> set[str]:
        counts = self._fallback_missing.copy()
        limit = settings.drama_fallback_missing_limit
        paused = excluded.copy()
        for name in sorted(self._fallback_dirs):
            if name in excluded:
                counts.pop(name, None)
                self._line(f"待续跑目录按当前规则排除: {name}")
                continue
            attempts = counts.get(name, 0)
            if name in share_names:
                counts.pop(name, None)
                attempts = 0
            elif attempts < limit and share_complete:
                attempts += 1
                counts[name] = attempts
            if attempts >= limit:
                paused.add(name)
                self._line(f"分享目录连续 {attempts} 次缺失，等待处理: {name}")
        if (counts != self._fallback_missing or excluded != self._fallback_excluded) and self._transfer_store is not None:
            self._transfer_store.set_fallback_missing(self._transfer_scope, counts, excluded=excluded)
            self._fallback_missing = counts
            self._fallback_excluded = excluded
        self.deferred_directories.extend(sorted(paused))
        return paused

    def _mark_ambiguous_directory(self, rel_label: str) -> None:
        self._line(f"无法唯一确认目标目录（重名或缺少 ID）: {rel_label}")
        self.incomplete_directories.append(rel_label)

    def _sync_share_dir_incremental(
        self,
        *,
        pwd_id: str,
        stoken: str,
        share_fid: str,
        share_name: str,
        dest_base_path: str,
        ignore_extension: bool,
        tree: Tree,
        rel_label: str,
        depth: int,
        copy_all: bool,
        known_dest_fid: str | None = None,
        known_missing: bool = False,
        verify_destination: bool = False,
        resume_copy: bool = False,
    ) -> list[str]:
        if depth >= settings.drama_sync_max_depth:
            self._line(f"层级过深，跳过: {rel_label}")
            self.incomplete_directories.append(rel_label)
            return []
        children, share_complete = self._fetch_share_items(pwd_id=pwd_id, stoken=stoken, pdir_fid=share_fid)
        if not share_complete:
            self._line(f"分享目录列表不完整: {rel_label}")
            self.incomplete_directories.append(rel_label)
            return []

        dest_path = f"{dest_base_path.rstrip('/')}/{share_name}" if share_name else dest_base_path
        try:
            if known_dest_fid:
                dest_fid = known_dest_fid
            elif known_missing:
                dest_fid = self._create_dest_dir_fid(dest_path)
            else:
                dest_fid = self._ensure_dest_dir_fid(dest_path)
        except Exception as exc:
            self._line(f"创建目标目录失败 {dest_path}: {exc}")
            self.incomplete_directories.append(rel_label)
            return []


        files = [item for item in children if not _is_dir(item)]
        dirs = [item for item in children if _is_dir(item)]
        if copy_all:
            dest_items, complete = [], False
        elif verify_destination:
            dest_items, complete = self._list_dest_for_diff(dest_fid, require_complete=True)
        else:
            dest_items, complete = self._list_dest_for_diff(dest_fid)
        existing_names, dest_dirs, ambiguous_dirs = self._dest_children(dest_items, ignore_extension)
        existing_exact = {_get_name(raw) for raw in dest_items if not _is_dir(raw)} if resume_copy else set()
        self._add_saved_names(existing_names)

        plan: list[DramaPlanItem] = []
        for item in self._filter_transfer_files(files):
            name = _get_name(item)
            fid = _get_fid(item)
            token = _get_fid_token(item)
            if not name or not fid:
                continue
            if resume_copy and name in existing_exact:
                continue
            if not copy_all and not resume_copy and _normalize_name(name, ignore_extension) in existing_names:
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
            sub_label = f"{rel_label}/{sub_name}"
            if sub_name in ambiguous_dirs:
                self._mark_ambiguous_directory(sub_label)
                continue
            transferred.extend(
                self._sync_share_dir_incremental(
                    pwd_id=pwd_id,
                    stoken=stoken,
                    share_fid=str(sub_fid),
                    share_name=sub_name,
                    dest_base_path=dest_path,
                    known_dest_fid=dest_dirs.get(sub_name),
                    known_missing=complete and sub_name not in dest_dirs,
                    ignore_extension=ignore_extension,
                    tree=tree,
                    rel_label=sub_label,
                    depth=depth + 1,
                    copy_all=copy_all,
                    verify_destination=verify_destination,
                    resume_copy=resume_copy,
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
        dest_complete: bool = False,
    ) -> list[str]:
        resuming = share_name in self._fallback_dirs
        if share_name in dest_dir_map and not resuming:
            self._line(f"跳过已存在的文件夹: {share_name}")
            hint = episode_from_folder_name(share_name)
            if hint:
                self._folder_episode_hints.append(int(hint))
            return []
        # 按目录 ID 直转无法检查其中的文件，存在文件规则时走逐文件路径。
        rules = self._file_filter_rules()
        if not resuming and share_token and not (rules.min_bytes > 0 or rules.blocked or rules.required or rules.min_date):
            try:
                plan = [
                    DramaPlanItem(
                        fid=share_fid,
                        fid_token=str(share_token),
                        origin_name=share_name,
                        target_name=share_name,
                    )
                ]
                saved = self._save_with_saved_fids(
                    pwd_id=pwd_id, stoken=stoken, dest_root_fid=dest_root_fid, plan=plan, is_dir=True
                )
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
        if self._transfer_store is not None and not resuming:
            self._transfer_store.set_fallback_dir(
                self._transfer_scope, share_name, active=True, fallback_scope=self._fallback_scope
            )
            self._fallback_dirs.add(share_name)
        previous_issues = len(self.incomplete_directories)
        saved = self._sync_share_dir_incremental(
            pwd_id=pwd_id,
            stoken=stoken,
            share_fid=share_fid,
            share_name=share_name,
            dest_base_path=savepath,
            known_dest_fid=dest_dir_map.get(share_name),
            known_missing=dest_complete and share_name not in dest_dir_map,
            ignore_extension=ignore_extension,
            tree=tree,
            rel_label=share_name,
            depth=0,
            copy_all=not resuming,
            verify_destination=resuming and getattr(self.adapter, "SUPPORTS_COMPLETE_LISTING", None) is True,
            resume_copy=resuming,
        )
        if self._transfer_store is not None and len(self.incomplete_directories) == previous_issues:
            self._transfer_store.set_fallback_dir(self._transfer_scope, share_name, active=False)
            self._fallback_dirs.discard(share_name)
        return saved

    def _record_saved_latest(self, file_names: list[str] | None = None, *, renamed_from: dict[str, str] | None = None) -> None:
        result = calculate_saved_latest(
            self.task_data, list(file_names) if file_names is not None else list(self._saved_name_pool),
            list(self._folder_episode_hints), at=datetime.now().astimezone().isoformat(timespec="seconds"),
            renamed_from=renamed_from,
        )
        if result is not None:
            self.saved_latest = result
