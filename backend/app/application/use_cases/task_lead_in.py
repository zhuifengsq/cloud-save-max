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

_LEAD_IN_LOCK_GUARD = threading.Lock()


_LEAD_IN_LOCKS: dict[tuple[str, str, str, str], threading.Lock] = {}


_LEAD_IN_KEYED_LOCKS = LRUKeyedLocks(maxsize=1024)


def _run_confirm_flow(
    adapter,
    *,
    dir_path: str,
    material_config: dict[str, Any],
    material_fid: str,
    material_name: str,
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

    material: dict[str, Any]
    if material_mode == lead_materials.MATERIAL_MODE_OWN_FILE:
        file_path = str(material_config.get("file_path") or "")
        resolver = getattr(adapter, "lead_in_resolve_own_file", None)
        if resolver is None:
            return finish("failed", "该网盘暂不支持本账号文件引流")
        own_response = lead_materials._safe_call(resolver, file_path)
        if not lead_materials._call_ok(own_response):
            return finish(
                "failed",
                str(own_response.get("message") or "素材文件不存在，请检查路径"),
            )
        file_info = lead_materials._own_file_data(own_response)
        if file_info is None or not file_info["name"]:
            return finish("failed", "素材文件不存在，请检查路径")
        material = {
            "source": lead_materials.MATERIAL_MODE_OWN_FILE,
            "fid": str(file_info.get("fid") or material_fid),
            "name": str(file_info.get("name") or material_name),
            "path": str(file_info.get("path") or file_path),
            "size": file_info.get("size"),
            "md5": str(file_info.get("md5") or ""),
            "account_name": str(material_config.get("account_name") or ""),
            "dir": False,
        }
    else:
        material_response = lead_materials._safe_call(
            adapter.lead_in_resolve_material,
            str(material_config.get("shareurl") or ""),
            str(material_config.get("passcode") or ""),
            material_fid,
        )
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
        material.setdefault("fid", material_fid)
        material.setdefault("name", material_name)
        material["source"] = lead_materials.MATERIAL_MODE_SHARE

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
            return result
        if not lead_settings.get_drama_lead_in_enabled(db):
            result["reason"] = "引流功能已关闭"
            return result
        material_config = lead_settings.get_drama_lead_in_material(db, drive_type)
        if material_config is None:
            result["reason"] = "该网盘未配置引流素材"
            return result
        mode = lead_materials.material_mode(material_config)
        if (
            mode == lead_materials.MATERIAL_MODE_OWN_FILE
            and not capability["own_file_supported"]
        ):
            result["reason"] = f"{capability['drive_name']}暂不支持本账号文件引流"
            return result
        if account is None or str(account.drive_type or "") != drive_type:
            account = lead_materials._default_account(db, drive_type)
        if account is None:
            result["reason"] = "网盘账号不可用"
            return result
        account_name = str(account.name or "")
        if mode == lead_materials.MATERIAL_MODE_OWN_FILE:
            material_account = str(material_config.get("account_name") or "")
            if material_account != account_name:
                result["reason"] = (
                    f"引流素材属于账号「{material_account}」，与任务使用的账号「{account_name}」不一致，"
                    "无法内部复制；请改用分享链接素材"
                )
                return result
        profile = resolve_drive_account_profile(account) or {}
        result["account"] = {
            "account_name": account_name,
            "nickname": str(profile.get("nickname") or ""),
        }
        dir_path = lead_materials.normalize_savepath(task_savepath)
        account = lead_materials.account_snapshot(account)
        end_read_tx(db)
        adapter = (
            self.provider.create(account)
            if self.provider is not None
            else lead_materials._build_adapter(account)
        )
        if adapter is None:
            result["reason"] = "网盘账号不可用"
            return result
        # 读取到此结束：网盘调用期间不持有数据库事务。
        end_read_tx(db)
        if not lead_materials._init_adapter(adapter):
            result["reason"] = "网盘账号不可用"
            return result

        dir_response = lead_materials._safe_call(adapter.lead_in_resolve_dir, dir_path)
        if not lead_materials._call_ok(dir_response):
            result["reason"] = lead_materials._directory_failure_reason(dir_response)
            return result
        dir_payload = dir_response.get("data") or {}
        dir_info = dir_payload.get("dir") if isinstance(dir_payload, dict) else None
        dir_info = dir_info if isinstance(dir_info, dict) else {}
        dir_fid = str(dir_info.get("fid") or "")
        result["directory"] = {
            "path": str(dir_info.get("path") or dir_path),
            "fid": dir_fid,
            "exists": True,
        }
        target = dir_payload if isinstance(dir_payload, dict) else {}

        material: dict[str, Any]
        material_fid = ""
        material_name = ""
        token_material_name = ""
        token_material_path = ""
        token_material_size: int | None = None
        if mode == lead_materials.MATERIAL_MODE_OWN_FILE:
            own_path = str(material_config.get("file_path") or "")
            resolver = getattr(adapter, "lead_in_resolve_own_file", None)
            if resolver is None:
                result["reason"] = f"{capability['drive_name']}暂不支持本账号文件引流"
                return result
            own_response = lead_materials._safe_call(resolver, own_path)
            if not lead_materials._call_ok(own_response):
                result["reason"] = str(
                    own_response.get("message") or "素材文件不存在，请检查路径"
                )
                return result
            file_info = lead_materials._own_file_data(own_response)
            if file_info is None or not file_info["name"]:
                result["reason"] = "素材文件不存在，请检查路径"
                return result
            material_fid = str(
                file_info.get("fid") or material_config.get("file_fid") or ""
            )
            material_name = str(
                file_info.get("name") or material_config.get("file_name") or ""
            )
            if not material_fid:
                result["reason"] = "引流素材不可用"
                return result
            material = {
                "source": lead_materials.MATERIAL_MODE_OWN_FILE,
                "fid": material_fid,
                "name": material_name,
                "path": str(file_info.get("path") or own_path),
                "size": file_info.get("size"),
                "md5": str(file_info.get("md5") or ""),
                "account_name": account_name,
                "dir": False,
            }
            result["material"] = {
                "name": material_name,
                "fid": material_fid,
                "source": lead_materials.MATERIAL_MODE_OWN_FILE,
                "account_name": account_name,
                "path": str(material.get("path") or own_path),
            }
            # 令牌里的名称/路径必须与保存的配置一致，确认时据此判断配置是否变化。
            token_material_name = str(material_config.get("file_name") or material_name)
            token_material_path = own_path
            token_material_size = file_info.get("size")
        else:
            material_response = lead_materials._safe_call(
                adapter.lead_in_resolve_material,
                str(material_config.get("shareurl") or ""),
                str(material_config.get("passcode") or ""),
                str(material_config.get("file_fid") or ""),
            )
            if not lead_materials._call_ok(material_response):
                result["reason"] = str(
                    material_response.get("message") or "引流素材不可用"
                )
                return result
            material_payload = material_response.get("data") or {}
            resolved_material = (
                material_payload.get("material")
                if isinstance(material_payload, dict)
                else None
            )
            material = (
                dict(resolved_material) if isinstance(resolved_material, dict) else {}
            )
            material_fid = str(
                material.get("fid") or material_config.get("file_fid") or ""
            )
            material_name = str(
                material.get("name") or material_config.get("file_name") or ""
            )
            if not material_fid:
                result["reason"] = "引流素材不可用"
                return result
            material["source"] = lead_materials.MATERIAL_MODE_SHARE
            material["fid"] = material_fid
            material["name"] = material_name
            result["material"] = {"name": material_name, "fid": material_fid}
            token_material_name = material_name
            token_material_path = ""
            raw_size = material.get("size")
            token_material_size = (
                raw_size
                if isinstance(raw_size, int) and not isinstance(raw_size, bool)
                else None
            )

        inspect_response = lead_materials._safe_call(
            adapter.lead_in_inspect, material, target
        )
        if not lead_materials._call_ok(inspect_response):
            result["reason"] = str(
                inspect_response.get("message") or "查重失败，请稍后重试"
            )
            return result
        inspect_data = inspect_response.get("data") or {}
        inspect_data = inspect_data if isinstance(inspect_data, dict) else {}
        result["state"] = str(inspect_data.get("state") or "absent")
        result["state_message"] = str(inspect_data.get("message") or "")

        record = lead_records.get_lead_in_record(db, task_id, drive_type)
        if result["state"] == "absent":
            # 目标目录里已经查不到该文件（例如用户在网盘手动删除）：清掉过期记录，
            # 让任务列表的「引流」列回到「—」，最近添加时间也一并消失。
            if record is not None:
                db.delete(record)
                db.commit()
        elif record is not None and record.status == "added":
            result["last_added_at"] = normalize_api_datetime(record.updated_at)

        ttl = lead_tokens.preview_token_ttl_seconds()
        expires_at = datetime.now(BEIJING_TZ) + timedelta(seconds=ttl)
        result["preview_token"] = lead_tokens.create_preview_token(
            {
                "task_id": task_id,
                "drive_type": drive_type,
                "account_name": account_name,
                "dir_path": dir_path,
                "dir_fid": dir_fid,
                "material_fid": material_fid,
                "material_name": token_material_name,
                "material_mode": mode,
                "material_account": (
                    str(material_config.get("account_name") or "")
                    if mode == lead_materials.MATERIAL_MODE_OWN_FILE
                    else ""
                ),
                "material_path": token_material_path,
                "material_size": token_material_size,
                "config_version": int(material_config.get("config_version") or 0),
                "exp": int(expires_at.timestamp()),
            }
        )
        result["expires_at"] = expires_at
        result["available"] = True
        return result

    def _confirm(
        self,
        db: Session,
        task: Task,
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
        if not drive_type or not account_name or not dir_path or not material_fid:
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
        if lead_materials.normalize_savepath(task_savepath) != dir_path:
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
                material_fid=material_fid,
                material_name=material_name,
                material_mode=selected_mode,
                finish=finish,
            )
        finally:
            lock.release()
