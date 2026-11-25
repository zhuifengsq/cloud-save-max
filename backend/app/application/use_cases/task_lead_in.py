from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.core.runtime.keyed_locks import LRUKeyedLocks
from app.core.settings import settings
from app.core.runtime.tx import end_read_tx
from app.application.drive_accounts import (
    BEIJING_TZ,
    get_active_account,
    normalize_api_datetime,
    resolve_drive_account_profile,
)

logger = logging.getLogger(__name__)

import app.application.lead_in.materials as lead_materials
import app.application.lead_in.records as lead_records
import app.application.lead_in.settings as lead_settings
import app.application.lead_in.tokens as lead_tokens
from app.application.task_share_links import normalize_savepath

_LEAD_IN_LOCK_GUARD = threading.Lock()


_LEAD_IN_LOCKS: dict[tuple[str, str, str, str], threading.Lock] = {}


_LEAD_IN_KEYED_LOCKS = LRUKeyedLocks(maxsize=1024)


def _resolved_directory_fid(target: dict[str, Any]) -> str:
    directory = target.get("dir")
    if not isinstance(directory, dict):
        return ""
    return str(directory.get("stable_fid", directory.get("fid")) or "").strip()


def _material_matches_preview(material: dict[str, Any], preview: dict[str, Any]) -> bool:
    if material.get("dir") or str(material.get("fid") or "") != preview["material_fid"]:
        return False
    name = preview.get("material_resolved_name", preview.get("material_name"))
    if str(material.get("name") or "") != name:
        return False
    path = preview.get("material_resolved_path", preview.get("material_path"))
    if path and material.get("path") != path:
        return False
    size = preview.get("material_size")
    if size is not None and material.get("size") != size:
        return False
    md5 = preview.get("material_md5")
    return not md5 or str(material.get("md5") or "").strip().lower() == md5


def _run_confirm_flow(
    adapter,
    *,
    dir_path: str,
    material_config: dict[str, Any],
    preview: dict[str, Any],
    material_mode: str,
    finish: Callable[[str, str], dict[str, Any]],
) -> dict[str, Any]:
    if not lead_materials._init_adapter(adapter):
        return finish("failed", "网盘账号不可用")

    dir_response = lead_materials._safe_call(adapter.lead_in_resolve_dir, dir_path)
    if not lead_materials._call_ok(dir_response):
        return finish("failed", lead_materials._directory_failure_reason(dir_response))
    target = dir_response.get("data") or {}
    target = target if isinstance(target, dict) else {}
    if _resolved_directory_fid(target) != preview["dir_fid"]:
        raise _stale_preview()

    material: dict[str, Any]
    if material_mode == lead_materials.MATERIAL_MODE_OWN_FILE:
        file_path = str(material_config.get("file_path") or "")
        resolver = getattr(adapter, "lead_in_resolve_own_file", None)
        if resolver is None:
            return finish("failed", "该网盘暂不支持本账号文件引流")
        own_response = lead_materials._safe_call(resolver, file_path)
        if lead_materials._error_code(own_response) == "LEAD_IN_MATERIAL_UNAVAILABLE":
            raise _stale_preview()
        if not lead_materials._call_ok(own_response):
            return finish(
                "failed",
                str(own_response.get("message") or "素材文件不存在，请检查路径"),
            )
        file_info = lead_materials._own_file_data(own_response)
        if file_info is None:
            raise _stale_preview()
        material = {
            **file_info,
            "source": lead_materials.MATERIAL_MODE_OWN_FILE,
            "account_name": str(material_config.get("account_name") or ""),
        }
    else:
        material_response = lead_materials._safe_call(
            adapter.lead_in_resolve_material,
            str(material_config.get("shareurl") or ""),
            str(material_config.get("passcode") or ""),
            preview["material_fid"],
        )
        if lead_materials._error_code(material_response) == "LEAD_IN_MATERIAL_UNAVAILABLE":
            raise _stale_preview()
        if not lead_materials._call_ok(material_response):
            return finish(
                "failed", str(material_response.get("message") or "引流素材不可用")
            )
        material_payload = material_response.get("data") or {}
        resolved_material = (
            material_payload.get("material")
            if isinstance(material_payload, dict)
            else None
        )
        material = (
            dict(resolved_material) if isinstance(resolved_material, dict) else {}
        )
        material["source"] = lead_materials.MATERIAL_MODE_SHARE

    if not _material_matches_preview(material, preview):
        raise _stale_preview()

    inspect_response = lead_materials._safe_call(
        adapter.lead_in_inspect, material, target
    )
    if not lead_materials._call_ok(inspect_response):
        return finish(
            "failed", str(inspect_response.get("message") or "查重失败，请稍后重试")
        )
    inspect_data = inspect_response.get("data") or {}
    inspect_data = inspect_data if isinstance(inspect_data, dict) else {}
    state = str(inspect_data.get("state") or "absent")
    if state == "exists":
        return finish(
            "exists",
            str(inspect_data.get("message") or "目标目录已存在该文件，无需重复添加"),
        )
    if state == "conflict":
        return finish(
            "conflict",
            str(inspect_data.get("message") or "目标目录存在同名文件，未做任何覆盖"),
        )

    if material_mode == lead_materials.MATERIAL_MODE_OWN_FILE:
        add_method = getattr(adapter, "lead_in_add_own_file", None)
        if add_method is None:
            return finish("failed", "该网盘暂不支持本账号文件引流")
    else:
        add_method = adapter.lead_in_add

    # 从这里开始向网盘提交写请求：之后任何异常/超时都只能报 pending，绝不谎报 added。
    add_response = lead_materials._safe_call(add_method, material, target)
    if not lead_materials._call_ok(add_response):
        if (
            lead_materials._is_pending_response(add_response)
            or lead_materials._error_code(add_response) == "LEAD_IN_ADAPTER_ERROR"
        ):
            return finish("pending", lead_materials._PENDING_MESSAGE)
        return finish("failed", str(add_response.get("message") or "添加引流文件失败"))
    add_payload = add_response.get("data") or {}
    handle = add_payload.get("handle") if isinstance(add_payload, dict) else None

    verify_response = lead_materials._safe_call(
        adapter.lead_in_verify, material, target, handle
    )
    if not lead_materials._call_ok(verify_response):
        if (
            lead_materials._is_pending_response(verify_response)
            or lead_materials._error_code(verify_response) == "LEAD_IN_ADAPTER_ERROR"
        ):
            return finish("pending", lead_materials._PENDING_MESSAGE)
        return finish(
            "failed", str(verify_response.get("message") or "核验添加结果失败")
        )
    verify_payload = verify_response.get("data") or {}
    verify_payload = verify_payload if isinstance(verify_payload, dict) else {}
    verify_state = str(verify_payload.get("state") or "pending")
    if verify_state == "added":
        return finish("added", str(verify_payload.get("message") or "引流文件已添加"))
    if verify_state == "pending":
        return finish(
            "pending",
            str(verify_payload.get("message") or lead_materials._PENDING_MESSAGE),
        )
    return finish("failed", str(verify_payload.get("message") or "添加引流文件失败"))


def _stale_preview() -> ApiError:
    return ApiError(
        code="LEAD_IN_STALE_PREVIEW",
        message="预览内容已变化，请重新预览",
        http_status=409,
    )


def lead_in_lock(
    drive_type: str, account_name: str, dir_path: str, material_name: str
) -> threading.Lock:
    """进程内并发锁：(网盘, 账号, 目录, 素材名) 维度，非阻塞获取。

    use_keyed_locks=True（默认）走 LRUKeyedLocks（上限 1024，淘汰未持有的锁）；
    置 False 回退到旧的无限 dict 实现。单进程假设：多 worker 时跨进程不互斥。
    """
    key = (
        str(drive_type or ""),
        str(account_name or ""),
        str(dir_path or ""),
        str(material_name or ""),
    )
    if settings.use_keyed_locks:
        return _LEAD_IN_KEYED_LOCKS.get(key)
    with _LEAD_IN_LOCK_GUARD:
        lock = _LEAD_IN_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LEAD_IN_LOCKS[key] = lock
        return lock


from dataclasses import dataclass
from app.application.tasks import get_task


@dataclass(frozen=True)
class LeadInCommand:
    task_id: int
    preview_token: str = ""
    actor_user_id: int | None = None
    ip: str | None = None
    user_agent: str | None = None


@dataclass(frozen=True)
class LeadInResult:
    data: dict[str, Any]


@dataclass(frozen=True)
class LeadInTask:
    id: int
    savepath: str
    account_name: str | None
    shareurl: str


@dataclass(frozen=True)
class _PreviewReadPlan:
    task_id: int
    drive_type: str
    capability: dict[str, Any]
    config: dict[str, Any]
    mode: str
    account_name: str
    account: Any
    dir_path: str


@dataclass(frozen=True)
class _PreviewMaterial:
    value: dict[str, Any]
    token_name: str
    token_path: str
    token_size: int | None


def _preview_token_payload(plan: _PreviewReadPlan, material: _PreviewMaterial,
                           target: dict[str, Any], expires_at: datetime) -> dict[str, Any]:
    """Pure token plan from detached inputs; never opens DB or drive IO."""
    return {
        "task_id": plan.task_id, "drive_type": plan.drive_type,
        "account_name": plan.account_name, "dir_path": plan.dir_path,
        "dir_fid": _resolved_directory_fid(target),
        "material_fid": str(material.value["fid"]), "material_name": material.token_name,
        "material_mode": plan.mode,
        "material_account": str(plan.config.get("account_name") or "")
            if plan.mode == lead_materials.MATERIAL_MODE_OWN_FILE else "",
        "material_path": material.token_path, "material_size": material.token_size,
        "material_resolved_name": material.value["name"],
        "material_resolved_path": str(material.value.get("path") or ""),
        "material_md5": str(material.value.get("md5") or "").strip().lower(),
        "config_version": int(plan.config.get("config_version") or 0),
        "exp": int(expires_at.timestamp()),
    }


class TaskLeadInUseCase:
    def __init__(self, db, *, provider=None):
        self.db = db
        self.provider = provider

    def _persist_result(self, db, **values):
        try:
            result = lead_records.store_result(db, **values)
            db.commit()
            return result
        except Exception:
            db.rollback()
            lead_materials.logger.warning(
                "Lead-in record commit failed task_id=%s", values.get("task_id")
            )
            raise

    def _task(self, task_id):
        task = get_task(self.db, task_id)
        return LeadInTask(
            int(task.id), str(task.savepath), task.account_name, str(task.shareurl)
        )

    def preview(self, command: LeadInCommand) -> LeadInResult:
        if self.db.new or self.db.dirty or self.db.deleted:
            raise RuntimeError('Lead-in preview requires a read-only session')
        return LeadInResult(self._preview(self.db, self._task(command.task_id)))

    def confirm(self, command: LeadInCommand) -> LeadInResult:
        return LeadInResult(
            self._confirm(
                self.db,
                self._task(command.task_id),
                command.preview_token,
                actor_user_id=command.actor_user_id,
                ip=command.ip,
                user_agent=command.user_agent,
            )
        )

    def _preview(self, db: Session, task: Any) -> dict[str, Any]:
        if db.new or db.dirty or db.deleted:
            raise RuntimeError('Lead-in preview requires a read-only session')
        result, plan = self._prepare_preview(db, task)
        if plan is None:
            return result
        end_read_tx(db)
        adapter = self.provider.create(plan.account) if self.provider is not None else lead_materials._build_adapter(plan.account)
        if adapter is None:
            result["reason"] = "网盘账号不可用"
            return result
        end_read_tx(db)
        if not lead_materials._init_adapter(adapter):
            result["reason"] = "网盘账号不可用"
            return result
        target = self._read_preview_directory(adapter, plan, result)
        if target is None:
            return result
        material = self._read_preview_material(adapter, plan, result)
        if material is None or not self._inspect_preview(adapter, material, target, result):
            return result
        self._sync_preview_record(db, plan, result)
        expires_at = datetime.now(BEIJING_TZ) + timedelta(seconds=lead_tokens.preview_token_ttl_seconds())
        result["preview_token"] = lead_tokens.create_preview_token(
            _preview_token_payload(plan, material, target, expires_at))
        result["expires_at"] = expires_at
        result["available"] = True
        return result

    def _prepare_preview(self, db: Session, task: Any) -> tuple[dict[str, Any], _PreviewReadPlan | None]:
        """按契约第 3.2 节的顺序生成预览；任何一步不可用都返回 available=False + 中文原因。"""
        task_id = int(task.id)
        task_savepath = str(getattr(task, "savepath", "") or "")
        drive_type, account = lead_materials._resolve_task_drive(db, task)
        capability = (
            lead_materials._drive_capability(drive_type) if drive_type else None
        )
        available = bool(capability and capability["supported"])
        result: dict[str, Any] = {
            "available": False,
            "reason": "",
            "drive": {
                "drive_type": drive_type,
                "drive_name": capability["drive_name"] if capability else "",
                "supported": available,
            },
            "account": None,
            "directory": None,
            "material": None,
            "state": "absent",
            "state_message": "",
            "last_added_at": None,
            "preview_token": "",
            "expires_at": None,
        }
        if not available:
            result["reason"] = "该网盘暂不支持引流"
            return result, None
        if not lead_settings.get_drama_lead_in_enabled(db):
            result["reason"] = "引流功能已关闭"
            return result, None
        material_config = lead_settings.get_drama_lead_in_material(db, drive_type)
        if material_config is None:
            result["reason"] = "该网盘未配置引流素材"
            return result, None
        mode = lead_materials.material_mode(material_config)
        if (
            mode == lead_materials.MATERIAL_MODE_OWN_FILE
            and not capability["own_file_supported"]
        ):
            result["reason"] = f"{capability['drive_name']}暂不支持本账号文件引流"
            return result, None
        if account is None or str(account.drive_type or "") != drive_type:
            account = lead_materials._default_account(db, drive_type)
        if account is None:
            result["reason"] = "网盘账号不可用"
            return result, None
        account_name = str(account.name or "")
        if mode == lead_materials.MATERIAL_MODE_OWN_FILE:
            material_account = str(material_config.get("account_name") or "")
            if material_account != account_name:
                result["reason"] = (
                    f"引流素材属于账号「{material_account}」，与任务使用的账号「{account_name}」不一致，"
                    "无法内部复制；请改用分享链接素材"
                )
                return result, None
        profile = resolve_drive_account_profile(account) or {}
        result["account"] = {
            "account_name": account_name,
            "nickname": str(profile.get("nickname") or ""),
        }
        dir_path = normalize_savepath(task_savepath)
        account = lead_materials.account_snapshot(account)
        return result, _PreviewReadPlan(task_id, drive_type, capability, material_config, mode,
                                        account_name, account, dir_path)

    def _read_preview_directory(self, adapter, plan: _PreviewReadPlan, result: dict[str, Any]) -> dict[str, Any] | None:
        response = lead_materials._safe_call(adapter.lead_in_resolve_dir, plan.dir_path)
        if not lead_materials._call_ok(response):
            result["reason"] = lead_materials._directory_failure_reason(response)
            return None
        payload = response.get("data") or {}
        target = payload if isinstance(payload, dict) else {}
        if not _resolved_directory_fid(target):
            result["reason"] = "保存目录无法精确定位"
            return None
        directory = target.get("dir")
        result["directory"] = {"path": str(directory.get("path") or plan.dir_path),
                               "fid": str(directory.get("fid") or ""), "exists": True}
        return target

    def _read_preview_material(self, adapter, plan: _PreviewReadPlan, result: dict[str, Any]) -> _PreviewMaterial | None:
        if plan.mode == lead_materials.MATERIAL_MODE_OWN_FILE:
            return self._read_own_preview_material(adapter, plan, result)
        return self._read_shared_preview_material(adapter, plan, result)

    def _read_own_preview_material(self, adapter, plan: _PreviewReadPlan, result: dict[str, Any]) -> _PreviewMaterial | None:
        config = plan.config
        own_path = str(config.get("file_path") or "")
        resolver = getattr(adapter, "lead_in_resolve_own_file", None)
        if resolver is None:
            result["reason"] = f"{plan.capability['drive_name']}暂不支持本账号文件引流"
            return None
        response = lead_materials._safe_call(resolver, own_path)
        if not lead_materials._call_ok(response):
            result["reason"] = str(response.get("message") or "素材文件不存在，请检查路径")
            return None
        file_info = lead_materials._own_file_data(response)
        if file_info is None or not file_info["name"] or file_info["dir"]:
            result["reason"] = "素材文件不存在，请检查路径"
            return None
        fid = file_info["fid"]
        name = file_info["name"]
        if not fid:
            result["reason"] = "引流素材不可用"
            return None
        material = {"source": lead_materials.MATERIAL_MODE_OWN_FILE, "fid": fid, "name": name,
                    "path": str(file_info.get("path") or own_path), "size": file_info.get("size"),
                    "md5": str(file_info.get("md5") or ""), "account_name": plan.account_name, "dir": False}
        result["material"] = {"name": name, "fid": fid, "source": lead_materials.MATERIAL_MODE_OWN_FILE,
                              "account_name": plan.account_name, "path": str(material.get("path") or own_path)}
        return _PreviewMaterial(material, str(config.get("file_name") or name), own_path, file_info.get("size"))

    def _read_shared_preview_material(self, adapter, plan: _PreviewReadPlan, result: dict[str, Any]) -> _PreviewMaterial | None:
        config = plan.config
        response = lead_materials._safe_call(adapter.lead_in_resolve_material,
            str(config.get("shareurl") or ""), str(config.get("passcode") or ""), str(config.get("file_fid") or ""))
        if not lead_materials._call_ok(response):
            result["reason"] = str(response.get("message") or "引流素材不可用")
            return None
        payload = response.get("data") or {}
        resolved = payload.get("material") if isinstance(payload, dict) else None
        material = dict(resolved) if isinstance(resolved, dict) else {}
        fid = str(material.get("fid") or "")
        name = str(material.get("name") or "")
        if not fid or not name or material.get("dir"):
            result["reason"] = "引流素材不可用"
            return None
        material.update(source=lead_materials.MATERIAL_MODE_SHARE, fid=fid, name=name)
        result["material"] = {"name": name, "fid": fid}
        size = material.get("size")
        return _PreviewMaterial(material, name, "", size if isinstance(size, int) and not isinstance(size, bool) else None)

    def _inspect_preview(self, adapter, material: _PreviewMaterial, target: dict[str, Any], result: dict[str, Any]) -> bool:
        response = lead_materials._safe_call(adapter.lead_in_inspect, material.value, target)
        if not lead_materials._call_ok(response):
            result["reason"] = str(response.get("message") or "查重失败，请稍后重试")
            return False
        data = response.get("data") or {}
        data = data if isinstance(data, dict) else {}
        result["state"] = str(data.get("state") or "absent")
        result["state_message"] = str(data.get("message") or "")
        return True

    def _sync_preview_record(self, db: Session, plan: _PreviewReadPlan, result: dict[str, Any]) -> None:
        """Short DB phase after all external reads; no task execution is persisted."""
        record = lead_records.get_lead_in_record(db, plan.task_id, plan.drive_type)
        if result["state"] == "absent":
            if record is not None:
                db.delete(record)
                db.commit()
        elif record is not None and record.status == "added":
            result["last_added_at"] = normalize_api_datetime(record.updated_at)

    def _confirm(
        self,
        db: Session,
        task: Any,
        preview_token: str,
        *,
        actor_user_id: int | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        """确认写入：校验预览令牌与配置版本 → 加进程内并发锁 → 查重 → 添加 → 核验 → 落库。"""
        task_id = int(task.id)
        task_savepath = str(getattr(task, "savepath", "") or "")
        payload = lead_tokens.parse_preview_token(preview_token)
        if payload is None or int(payload.get("task_id") or 0) != task_id:
            raise _stale_preview()

        drive_type = str(payload.get("drive_type") or "")
        account_name = str(payload.get("account_name") or "")
        dir_path = str(payload.get("dir_path") or "")
        material_fid = str(payload.get("material_fid") or "")
        material_name = str(payload.get("material_name") or "")
        selected_mode = str(payload.get("material_mode") or "")
        material_account = str(payload.get("material_account") or "")
        material_path = str(payload.get("material_path") or "")
        config_version = int(payload.get("config_version") or 0)
        if not drive_type or not account_name or not dir_path or not payload.get("dir_fid") or not material_fid:
            raise _stale_preview()
        if selected_mode not in lead_materials.MATERIAL_MODES:
            raise _stale_preview()
        if selected_mode == lead_materials.MATERIAL_MODE_OWN_FILE and (
            not material_account or not material_path or not material_name
        ):
            raise _stale_preview()

        capability = lead_materials._drive_capability(drive_type)
        if capability is None or not capability["supported"]:
            raise _stale_preview()
        if (
            selected_mode == lead_materials.MATERIAL_MODE_OWN_FILE
            and not capability["own_file_supported"]
        ):
            raise _stale_preview()
        if not lead_settings.get_drama_lead_in_enabled(db):
            raise _stale_preview()
        if normalize_savepath(task_savepath) != dir_path:
            raise _stale_preview()
        material_config = lead_settings.get_drama_lead_in_material(db, drive_type)
        if material_config is None:
            raise _stale_preview()
        if int(material_config.get("config_version") or 0) != config_version:
            raise _stale_preview()
        if lead_materials.material_mode(material_config) != selected_mode:
            raise _stale_preview()
        if selected_mode == lead_materials.MATERIAL_MODE_OWN_FILE:
            if str(material_config.get("account_name") or "") != material_account:
                raise _stale_preview()
            if str(material_config.get("file_path") or "") != material_path:
                raise _stale_preview()
            if str(material_config.get("file_name") or "") != material_name:
                raise _stale_preview()
        else:
            if str(material_config.get("file_fid") or "") != material_fid:
                raise _stale_preview()
            if str(material_config.get("file_name") or "") != material_name:
                raise _stale_preview()

        account = get_active_account(db, account_name)
        if account is None or str(account.drive_type or "") != drive_type:
            raise _stale_preview()
        if (
            selected_mode == lead_materials.MATERIAL_MODE_OWN_FILE
            and material_account != account_name
        ):
            raise _stale_preview()
        current_drive, current_account = lead_materials._resolve_task_drive(db, task)
        if (
            current_account is None
            or str(current_account.drive_type or "") != current_drive
        ):
            current_account = lead_materials._default_account(db, current_drive)
        if (
            current_drive != drive_type
            or current_account is None
            or str(current_account.name or "") != account_name
        ):
            raise _stale_preview()

        lock = lead_in_lock(drive_type, account_name, dir_path, material_name)
        if not lock.acquire(blocking=False):
            raise ApiError(
                code="LEAD_IN_IN_PROGRESS",
                message="该目录正在添加同名引流文件，请稍后再试",
                http_status=409,
            )
        try:
            account = lead_materials.account_snapshot(account)
            end_read_tx(db)
            adapter = (
                self.provider.create(account)
                if self.provider is not None
                else lead_materials._build_adapter(account)
            )
            if adapter is None:
                return self._persist_result(
                    db,
                    task_id=task_id,
                    drive_type=drive_type,
                    account_name=account_name,
                    dir_path=dir_path,
                    material_name=material_name,
                    material_fid=material_fid,
                    status="failed",
                    message="网盘账号不可用",
                    config_version=config_version,
                    actor_user_id=actor_user_id,
                    ip=ip,
                    user_agent=user_agent,
                )
            # 结束只读事务：网盘调用期间不持有数据库事务。
            end_read_tx(db)

            def finish(status: str, message: str) -> dict[str, Any]:
                return self._persist_result(
                    db,
                    task_id=task_id,
                    drive_type=drive_type,
                    account_name=account_name,
                    dir_path=dir_path,
                    material_name=material_name,
                    material_fid=material_fid,
                    status=status,
                    message=message,
                    config_version=config_version,
                    actor_user_id=actor_user_id,
                    ip=ip,
                    user_agent=user_agent,
                )

            return _run_confirm_flow(
                adapter,
                dir_path=dir_path,
                material_config=material_config,
                preview=payload,
                material_mode=selected_mode,
                finish=finish,
            )
        finally:
            lock.release()
