"""追剧任务引流文件服务：配置读写、素材扫描、预览、确认与记录查询。

设计约束：
1. 这里只调用适配器统一的 lead_in_* 能力接口，不出现任何网盘类型分支；
2. 网盘调用（登录、解析、查重、添加、核验）期间不持有数据库写事务；
3. 普通接口与审计不返回提取码、完整分享链接。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ApiError, bad_request
from app.core.runtime.keyed_locks import LRUKeyedLocks
from app.core.settings import settings
from app.core.runtime.tx import end_read_tx
from app.infrastructure.drive.factory import AdapterFactory
from app.infrastructure.drive.adapter_registry import AdapterRegistry
from app.models.drive_account import DriveAccount
from app.models.system_setting import SystemSetting
from app.models.task import Task
from app.models.task_lead_in import TaskLeadInRecord
from app.application.audit import write_audit_log
from app.application.drive_accounts import (
    BEIJING_TZ,
    get_active_account,
    normalize_api_datetime,
    pick_default_account_name,
    resolve_drive_account_profile,
)

logger = logging.getLogger(__name__)

SETTING_KEY_ENABLED = "drama_lead_in_enabled"
SETTING_KEY_MATERIALS = "drama_lead_in_materials"
SETTING_DESCRIPTION_ENABLED = "追剧任务引流文件总开关"
SETTING_DESCRIPTION_MATERIALS = "追剧任务引流素材配置（按网盘保存）"
SETTINGS_VERSION = 1

STATUSES = ("added", "exists", "conflict", "pending", "failed")
_ACTIVE_STATUSES = set(STATUSES)
_PENDING_MESSAGE = "网盘响应超时，添加结果待确认"

MATERIAL_MODE_SHARE = "share"
MATERIAL_MODE_OWN_FILE = "own_file"
MATERIAL_MODES = (MATERIAL_MODE_SHARE, MATERIAL_MODE_OWN_FILE)

_LEAD_IN_LOCK_GUARD = threading.Lock()
_LEAD_IN_LOCKS: dict[tuple[str, str, str, str], threading.Lock] = {}
# 灰度开关 use_keyed_locks=True 时用这把有上限的锁表：key 含目录与素材名，
# 基数随运营配置（网盘 × 账号 × 目录 × 素材）增长，旧 dict 只增不减。
_LEAD_IN_KEYED_LOCKS = LRUKeyedLocks(maxsize=1024)


# ---------------------------------------------------------------------------
# 配置读写
# ---------------------------------------------------------------------------


def _get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.execute(select(SystemSetting).where(SystemSetting.key == key)).scalars().first()
    if row is None or row.value is None:
        return default
    return row.value


def _set_setting(db: Session, key: str, value: str, description: str = "") -> None:
    row = db.execute(select(SystemSetting).where(SystemSetting.key == key)).scalars().first()
    if row is not None:
        row.value = value
        if description:
            row.description = description
        return
    db.add(SystemSetting(key=key, value=value, description=description or None))


def get_drama_lead_in_enabled(db: Session) -> bool:
    return _get_setting(db, SETTING_KEY_ENABLED, "0").strip() == "1"


def get_drama_lead_in_materials(db: Session) -> dict[str, dict[str, Any]]:
    raw = _get_setting(db, SETTING_KEY_MATERIALS, "{}")
    try:
        payload = json.loads(raw) if raw else {}
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for key, value in payload.items():
        if isinstance(key, str) and isinstance(value, dict):
            result[key] = dict(value)
    return result


def material_mode(item: dict[str, Any] | None) -> str:
    """素材模式：历史配置没有 mode 字段时一律按 share 处理。"""
    if not isinstance(item, dict):
        return MATERIAL_MODE_SHARE
    mode = str(item.get("mode") or "").strip()
    return mode if mode in MATERIAL_MODES else MATERIAL_MODE_SHARE


def get_drama_lead_in_material(db: Session, drive_type: str) -> dict[str, Any] | None:
    """取某网盘已保存且可用的素材配置；未配置或缺少必需字段时返回 None。"""
    item = get_drama_lead_in_materials(db).get(str(drive_type or ""))
    if not isinstance(item, dict):
        return None
    mode = material_mode(item)
    if mode == MATERIAL_MODE_OWN_FILE:
        if not str(item.get("account_name") or "").strip():
            return None
        if not str(item.get("file_path") or "").strip():
            return None
        if not str(item.get("file_name") or "").strip():
            return None
    else:
        if not str(item.get("shareurl") or "").strip():
            return None
        if not str(item.get("file_fid") or "").strip():
            return None
    normalized = dict(item)
    normalized["mode"] = mode
    return normalized


def mask_shareurl(value: str) -> str:
    """分享链接脱敏：只保留 scheme + host + 路径前 6 个字符。"""
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        head = raw[:6]
        return f"{head}***" if len(raw) > 6 else "***"
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path[:6]}***"


# ---------------------------------------------------------------------------
# 网盘能力（遍历适配器注册表，禁止硬编码网盘类型）
# ---------------------------------------------------------------------------


def _drive_capability(drive_type: str) -> dict[str, Any] | None:
    adapter_class = AdapterFactory.ADAPTER_MAP.get(str(drive_type or ""))
    if adapter_class is None:
        return None
    drive_name = str(getattr(adapter_class, "DRIVE_NAME", "") or drive_type)
    supported = bool(getattr(adapter_class, "SUPPORTS_DRAMA_LEAD_IN", False))
    own_file_supported = bool(getattr(adapter_class, "SUPPORTS_LEAD_IN_OWN_FILE", False))
    own_file_browse_supported = bool(
        getattr(adapter_class, "SUPPORTS_LEAD_IN_OWN_BROWSE", False)
    )
    return {
        "drive_type": str(drive_type),
        "drive_name": drive_name,
        "supported": supported,
        "own_file_supported": own_file_supported,
        "own_file_browse_supported": own_file_browse_supported,
        "reason": "" if supported else f"{drive_name}暂不支持引流",
    }


def list_drive_capabilities() -> list[dict[str, Any]]:
    return [
        capability
        for capability in (_drive_capability(drive_type) for drive_type in AdapterFactory.ADAPTER_MAP.keys())
        if capability is not None
    ]


def build_settings_payload(db: Session) -> dict[str, Any]:
    enabled = get_drama_lead_in_enabled(db)
    materials = get_drama_lead_in_materials(db)
    drives: list[dict[str, Any]] = []
    for capability in list_drive_capabilities():
        drive_type = capability["drive_type"]
        item = materials.get(drive_type)
        item = item if isinstance(item, dict) else {}
        mode = material_mode(item)
        shareurl = str(item.get("shareurl") or "")
        account_name = str(item.get("account_name") or "")
        file_path = str(item.get("file_path") or "")
        file_name = str(item.get("file_name") or "")
        raw_size = item.get("file_size")
        file_size = raw_size if isinstance(raw_size, int) and not isinstance(raw_size, bool) else None
        if mode == MATERIAL_MODE_OWN_FILE:
            configured = bool(account_name and file_path and file_name)
        else:
            configured = bool(shareurl and str(item.get("file_fid") or "").strip())
        drives.append(
            {
                "drive_type": drive_type,
                "drive_name": capability["drive_name"],
                "supported": capability["supported"],
                "reason": capability["reason"],
                "own_file_supported": bool(capability.get("own_file_supported")),
                "own_file_browse_supported": bool(
                    capability.get("own_file_browse_supported")
                ),
                "mode": mode,
                "configured": configured,
                "shareurl": mask_shareurl(shareurl),
                "passcode_set": bool(str(item.get("passcode") or "").strip()),
                "account_name": account_name,
                "file_path": file_path,
                "file_size": file_size,
                "file_fid": str(item.get("file_fid") or ""),
                "file_name": file_name,
                "config_version": int(item.get("config_version") or 0),
                "updated_at": item.get("updated_at") or None,
            }
        )
    return {"enabled": enabled, "version": SETTINGS_VERSION, "drives": drives}


def update_drama_lead_in_settings(db: Session, data: dict[str, Any]) -> dict[str, Any]:
    """保存引流配置：先校验能力，再用适配器真实解析素材，全部成功后才写库。"""
    enabled = bool((data or {}).get("enabled"))
    raw_items = (data or {}).get("materials") or []
    materials = get_drama_lead_in_materials(db)
    versions = {
        key: int(value.get("config_version") or 0)
        for key, value in materials.items()
        if isinstance(value, dict)
    }

    prepared: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        drive_type = str(raw.get("drive_type") or "").strip()
        capability = _drive_capability(drive_type)
        if capability is None:
            raise bad_request("LEAD_IN_UNSUPPORTED", "未知的网盘类型")
        if not capability["supported"]:
            raise bad_request("LEAD_IN_UNSUPPORTED", capability["reason"] or "该网盘暂不支持引流")
        mode = str(raw.get("mode") or MATERIAL_MODE_SHARE).strip() or MATERIAL_MODE_SHARE
        if mode not in MATERIAL_MODES:
            raise bad_request("LEAD_IN_INVALID_MODE", "不支持的素材来源")
        if mode == MATERIAL_MODE_OWN_FILE and not capability["own_file_supported"]:
            raise bad_request(
                "LEAD_IN_UNSUPPORTED", f"{capability['drive_name']}暂不支持本账号文件引流"
            )
        existing = materials.get(drive_type)
        existing = existing if isinstance(existing, dict) else {}
        shareurl = str(raw.get("shareurl") or "").strip()
        account_name = str(raw.get("account_name") or "").strip()
        file_path = str(raw.get("file_path") or "").strip()
        if mode == MATERIAL_MODE_OWN_FILE:
            clear = not account_name or not file_path
        else:
            clear = not shareurl
        prepared.append(
            {
                "drive_type": drive_type,
                "mode": mode,
                "shareurl": shareurl,
                "passcode": str(raw.get("passcode") or "").strip()
                or str(existing.get("passcode") or ""),
                "account_name": account_name,
                "file_path": file_path,
                "file_fid": str(raw.get("file_fid") or "").strip(),
                "file_name": str(raw.get("file_name") or "").strip(),
                "existing": existing,
                "clear": clear,
            }
        )

    # 先结束只读事务：随后的网盘登录/解析期间不能持有数据库事务。
    end_read_tx(db)

    resolved: list[dict[str, Any]] = []
    for item in prepared:
        if item["clear"]:
            resolved.append(item)
            continue
        drive_type = item["drive_type"]
        if item["mode"] == MATERIAL_MODE_OWN_FILE:
            account = get_active_account(db, item["account_name"])
            if account is None or str(account.drive_type or "") != drive_type:
                raise bad_request(
                    "LEAD_IN_ACCOUNT_UNAVAILABLE", "网盘账号不可用，请先在网盘账号页添加该网盘账号"
                )
            adapter = _build_adapter(account)
            if adapter is None:
                raise bad_request("LEAD_IN_ACCOUNT_UNAVAILABLE", "网盘账号不可用，请检查账号配置")
            end_read_tx(db)
            if not _init_adapter(adapter):
                raise bad_request("LEAD_IN_ACCOUNT_UNAVAILABLE", "网盘账号不可用（登录失败），请检查账号状态")
            resolver = getattr(adapter, "lead_in_resolve_own_file", None)
            if resolver is None:
                raise bad_request("LEAD_IN_UNSUPPORTED", "该网盘暂不支持本账号文件引流")
            response = _safe_call(resolver, item["file_path"])
            if not _call_ok(response):
                raise _lead_in_error(response, "LEAD_IN_MATERIAL_RESOLVE_FAILED", "素材文件解析失败")
            file_info = _own_file_data(response)
            if file_info is None or not file_info["name"]:
                raise bad_request("LEAD_IN_MATERIAL_UNAVAILABLE", "素材文件不存在，请检查路径")
            resolved.append({**item, "own_file": file_info})
            continue

        account = _default_account(db, drive_type)
        if account is None:
            raise bad_request("LEAD_IN_ACCOUNT_UNAVAILABLE", "网盘账号不可用，请先在网盘账号页添加该网盘账号")
        adapter = _build_adapter(account)
        if adapter is None:
            raise bad_request("LEAD_IN_ACCOUNT_UNAVAILABLE", "网盘账号不可用，请检查账号配置")
        end_read_tx(db)
        if not _init_adapter(adapter):
            raise bad_request("LEAD_IN_ACCOUNT_UNAVAILABLE", "网盘账号不可用（登录失败），请检查账号状态")
        response = _safe_call(
            adapter.lead_in_resolve_material,
            item["shareurl"],
            item["passcode"],
            item["file_fid"],
        )
        if not _call_ok(response):
            code = _error_code(response) or "LEAD_IN_MATERIAL_RESOLVE_FAILED"
            message = str(response.get("message") or "引流素材解析失败")
            if not code.startswith("LEAD_IN_"):
                code = "LEAD_IN_MATERIAL_RESOLVE_FAILED"
            raise bad_request(code, message)
        payload = response.get("data") or {}
        material = payload.get("material") if isinstance(payload, dict) else None
        material = material if isinstance(material, dict) else {}
        resolved.append(
            {
                **item,
                "material_fid": str(material.get("fid") or item["file_fid"] or ""),
                "material_name": str(material.get("name") or item["file_name"] or ""),
            }
        )

    # 所有网盘都解析成功后才真正改库，避免保存一半。
    updated_at = datetime.now(BEIJING_TZ).replace(microsecond=0).isoformat()
    for item in resolved:
        drive_type = item["drive_type"]
        if item["clear"]:
            materials.pop(drive_type, None)
            continue
        config_version = versions.get(drive_type, 0) + 1
        if item["mode"] == MATERIAL_MODE_OWN_FILE:
            file_info = item.get("own_file") or {}
            materials[drive_type] = {
                "mode": MATERIAL_MODE_OWN_FILE,
                "account_name": item["account_name"],
                "file_path": item["file_path"],
                "file_fid": str(file_info.get("fid") or ""),
                "file_name": str(file_info.get("name") or ""),
                "file_size": file_info.get("size"),
                "file_md5": str(file_info.get("md5") or ""),
                "config_version": config_version,
                "updated_at": updated_at,
            }
            continue
        material_fid = item.get("material_fid") or ""
        if not material_fid:
            raise bad_request("LEAD_IN_MATERIAL_RESOLVE_FAILED", "未能定位到唯一的素材文件，请先扫描并选择")
        materials[drive_type] = {
            "mode": MATERIAL_MODE_SHARE,
            "shareurl": item["shareurl"],
            "passcode": item["passcode"],
            "file_fid": material_fid,
            "file_name": item.get("material_name") or "",
            "config_version": config_version,
            "updated_at": updated_at,
        }

    _set_setting(db, SETTING_KEY_ENABLED, "1" if enabled else "0", SETTING_DESCRIPTION_ENABLED)
    _set_setting(db, SETTING_KEY_MATERIALS, json.dumps(materials, ensure_ascii=False), SETTING_DESCRIPTION_MATERIALS)
    # 提交归属：写设置 + 审计由路由的事务边界统一提交（原实现在此提前 commit）。
    db.flush()
    return build_settings_payload(db)


def scan_drama_lead_in_material(db: Session, drive_type: str, shareurl: str, passcode: str) -> dict[str, Any]:
    """列出分享内文件供前端选择素材；适配器判定多文件歧义但返回 files 时按成功处理。"""
    drive_type = str(drive_type or "").strip()
    capability = _drive_capability(drive_type)
    if capability is None:
        raise bad_request("LEAD_IN_UNSUPPORTED", "未知的网盘类型")
    if not capability["supported"]:
        raise bad_request("LEAD_IN_UNSUPPORTED", capability["reason"] or "该网盘暂不支持引流")
    shareurl = str(shareurl or "").strip()
    if not shareurl:
        raise bad_request("LEAD_IN_SHAREURL_REQUIRED", "请填写素材分享链接")
    account = _default_account(db, drive_type)
    if account is None:
        raise bad_request("LEAD_IN_ACCOUNT_UNAVAILABLE", "网盘账号不可用，请先在网盘账号页添加该网盘账号")
    adapter = _build_adapter(account)
    if adapter is None:
        raise bad_request("LEAD_IN_ACCOUNT_UNAVAILABLE", "网盘账号不可用，请检查账号配置")
    end_read_tx(db)
    if not _init_adapter(adapter):
        raise bad_request("LEAD_IN_ACCOUNT_UNAVAILABLE", "网盘账号不可用（登录失败），请检查账号状态")
    response = _safe_call(adapter.lead_in_resolve_material, shareurl, str(passcode or ""), "")
    payload = response.get("data") or {}
    files = _normalize_scan_files(payload.get("files") if isinstance(payload, dict) else None)
    if _call_ok(response):
        material = payload.get("material") if isinstance(payload, dict) else None
        material = material if isinstance(material, dict) else {}
        return {"files": files, "selected_fid": str(material.get("fid") or "")}
    code = _error_code(response)
    if code == "LEAD_IN_MATERIAL_AMBIGUOUS" and files:
        return {"files": files, "selected_fid": ""}
    if not code.startswith("LEAD_IN_"):
        code = "LEAD_IN_SCAN_FAILED"
    raise bad_request(code, str(response.get("message") or "读取分享文件失败"))


def resolve_drama_lead_in_own_file(
    db: Session, drive_type: str, account_name: str, file_path: str
) -> dict[str, Any]:
    """校验并返回 own_file 模式选中的文件信息（供设置页「读取文件」按钮）。"""
    adapter, _capability, path = _own_file_context(
        db, drive_type, account_name, file_path=file_path
    )
    resolver = getattr(adapter, "lead_in_resolve_own_file", None)
    if resolver is None:
        raise bad_request("LEAD_IN_UNSUPPORTED", "该网盘暂不支持本账号文件引流")
    response = _safe_call(resolver, path)
    if not _call_ok(response):
        raise _lead_in_error(response, "LEAD_IN_MATERIAL_RESOLVE_FAILED", "素材文件解析失败")
    file_info = _own_file_data(response)
    if file_info is None or not file_info["name"]:
        raise bad_request("LEAD_IN_MATERIAL_UNAVAILABLE", "素材文件不存在，请检查路径")
    return {"file": file_info}


def list_drama_lead_in_own_dir(
    db: Session, drive_type: str, account_name: str, dir_path: str
) -> dict[str, Any]:
    """浏览本账号目录（只读），返回 {path, fid, entries:[{fid,name,dir,size,path}]}。"""
    adapter, capability, path = _own_file_context(
        db, drive_type, account_name, browse=True, file_path=dir_path
    )
    lister = getattr(adapter, "lead_in_list_own_dir", None)
    if lister is None:
        raise bad_request(
            "LEAD_IN_UNSUPPORTED",
            f"{capability['drive_name']}暂不支持浏览本账号目录",
        )
    response = _safe_call(lister, path)
    if not _call_ok(response):
        raise _lead_in_error(response, "LEAD_IN_DIR_LIST_FAILED", "读取目录失败，请稍后重试")
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    return {
        "path": str(data.get("path") or path),
        "fid": str(data.get("fid") or ""),
        "entries": _normalize_dir_entries(data.get("entries")),
    }


# ---------------------------------------------------------------------------
# 账号与适配器
# ---------------------------------------------------------------------------


def _default_account(db: Session, drive_type: str) -> DriveAccount | None:
    account_name = pick_default_account_name(db, drive_type)
    if not account_name:
        return None
    return get_active_account(db, account_name)


def _build_adapter(account: DriveAccount):
    if account is None:
        return None
    try:
        drive_type = str(account.drive_type or "")
        config = AdapterRegistry.parse_config_json(
            drive_type,
            getattr(account, "config_json", "") or "",
            getattr(account, "cookie", "") or "",
        )
        cookie = AdapterRegistry.serialize_config(drive_type, config)
        return AdapterFactory.create_adapter(
            drive_type,
            cookie,
            0,
            config=config,
            account_name=account.name,
            no_login=False,
        )
    except Exception:
        logger.warning("创建引流适配器失败: %s", getattr(account, "name", ""), exc_info=True)
        return None


def _init_adapter(adapter) -> bool:
    if adapter is None:
        return False
    try:
        adapter.ensure_initialized(ttl_seconds=AdapterFactory.init_ttl_seconds())
        return True
    except Exception:
        logger.warning("引流适配器初始化失败", exc_info=True)
        return False


def _own_file_context(
    db: Session,
    drive_type: str,
    account_name: str,
    *,
    browse: bool = False,
    file_path: str | None = None,
) -> tuple[Any, dict[str, Any], str]:
    """校验网盘能力、路径与账号，返回 (已初始化适配器, 能力信息, 规范化路径)。

    「读取本账号文件」与「浏览本账号目录」共用，避免复制能力/账号/适配器初始化逻辑；
    browse=True 时额外要求目录浏览能力，并允许根目录。
    """
    drive_type = str(drive_type or "").strip()
    capability = _drive_capability(drive_type)
    if capability is None:
        raise bad_request("LEAD_IN_UNSUPPORTED", "未知的网盘类型")
    if not capability["supported"]:
        raise bad_request("LEAD_IN_UNSUPPORTED", capability["reason"] or "该网盘暂不支持引流")
    if browse:
        if not capability["own_file_browse_supported"]:
            raise bad_request(
                "LEAD_IN_UNSUPPORTED",
                f"{capability['drive_name']}暂不支持浏览本账号目录",
            )
    elif not capability["own_file_supported"]:
        raise bad_request(
            "LEAD_IN_UNSUPPORTED", f"{capability['drive_name']}暂不支持本账号文件引流"
        )

    path = str(file_path or "").strip()
    if browse:
        path = path or "/"
        if path != "/" and not _valid_own_file_path(path):
            raise bad_request("LEAD_IN_DIR_INVALID", "目录路径不合法")
    elif file_path is not None and not _valid_own_file_path(path):
        raise bad_request("LEAD_IN_DIR_INVALID", "请填写账号内文件的完整路径")

    account = get_active_account(db, str(account_name or "").strip())
    if account is None or str(account.drive_type or "") != drive_type:
        raise bad_request("LEAD_IN_ACCOUNT_UNAVAILABLE", "网盘账号不可用，请先在网盘账号页添加该网盘账号")
    adapter = _build_adapter(account)
    if adapter is None:
        raise bad_request("LEAD_IN_ACCOUNT_UNAVAILABLE", "网盘账号不可用，请检查账号配置")
    end_read_tx(db)
    if not _init_adapter(adapter):
        raise bad_request("LEAD_IN_ACCOUNT_UNAVAILABLE", "网盘账号不可用（登录失败），请检查账号状态")
    return adapter, capability, path


def _safe_call(func: Callable[..., Any], *args) -> dict[str, Any]:
    try:
        response = func(*args)
    except Exception as exc:
        return {
            "code": "LEAD_IN_TIMEOUT" if _is_timeout_error(exc) else "LEAD_IN_ADAPTER_ERROR",
            "message": _friendly_error(exc),
            "data": None,
        }
    if not isinstance(response, dict):
        return {"code": "LEAD_IN_ADAPTER_ERROR", "message": "网盘响应异常，请稍后重试", "data": None}
    return response


def _call_ok(response: Any) -> bool:
    return isinstance(response, dict) and response.get("code") in (0, "0")


def _error_code(response: Any) -> str:
    if not isinstance(response, dict):
        return ""
    code = response.get("code")
    if code is None or code == 0:
        return ""
    return str(code)


def _is_timeout_error(exc: Exception) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    text = str(exc).lower()
    return "timeout" in text or "timed out" in text or "超时" in text


def _friendly_error(exc: Exception) -> str:
    if _is_timeout_error(exc):
        return "网盘响应超时，请稍后重试"
    return "网盘调用失败，请稍后重试"


def _is_pending_response(response: Any) -> bool:
    code = _error_code(response)
    if code in ("LEAD_IN_TIMEOUT", "LEAD_IN_PENDING"):
        return True
    message = str(response.get("message") or "") if isinstance(response, dict) else ""
    lowered = message.lower()
    return "超时" in message or "timeout" in lowered or "unknown" in lowered and "pending" in lowered


def _directory_failure_reason(response: Any) -> str:
    code = _error_code(response)
    if code in ("LEAD_IN_DIR_NOT_FOUND", "LEAD_IN_DIR_INVALID"):
        return "任务保存目录不存在"
    message = str(response.get("message") or "").strip() if isinstance(response, dict) else ""
    return message or "任务保存目录不可用"


def _normalize_scan_files(raw: Any) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        size = item.get("size")
        files.append(
            {
                "fid": str(item.get("fid") or ""),
                "name": str(item.get("name") or ""),
                "size": size if isinstance(size, int) and not isinstance(size, bool) else None,
                "dir": bool(item.get("dir")),
                "path": str(item.get("path") or ""),
            }
        )
    return files


def _normalize_dir_entries(raw: Any) -> list[dict[str, Any]]:
    """目录浏览条目归一化：只保留契约字段，最多 200 条。"""
    entries: list[dict[str, Any]] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if not name:
            continue
        size = item.get("size")
        entries.append(
            {
                "fid": str(item.get("fid") or ""),
                "name": name,
                "dir": bool(item.get("dir")),
                "size": size if isinstance(size, int) and not isinstance(size, bool) else None,
                "path": str(item.get("path") or ""),
            }
        )
        if len(entries) >= 200:
            break
    return entries


def _own_file_data(response: Any) -> dict[str, Any] | None:
    payload = response.get("data") if isinstance(response, dict) else None
    file_info = payload.get("file") if isinstance(payload, dict) else None
    if not isinstance(file_info, dict):
        return None
    raw_size = file_info.get("size")
    return {
        "fid": str(file_info.get("fid") or ""),
        "name": str(file_info.get("name") or ""),
        "path": str(file_info.get("path") or ""),
        "size": raw_size if isinstance(raw_size, int) and not isinstance(raw_size, bool) else None,
        "md5": str(file_info.get("md5") or ""),
        "dir": bool(file_info.get("dir")),
    }


def _valid_own_file_path(path: str) -> bool:
    raw = str(path or "").strip()
    if not raw or raw == "/" or not raw.startswith("/"):
        return False
    if ".." in raw or "://" in raw:
        return False
    return True


def _lead_in_error(response: Any, default_code: str, default_message: str) -> ApiError:
    code = _error_code(response) or default_code
    message = str(response.get("message") or default_message) if isinstance(response, dict) else default_message
    if not code.startswith("LEAD_IN_"):
        code = default_code
    return bad_request(code, message)


# ---------------------------------------------------------------------------
# 任务、目录与预览
# ---------------------------------------------------------------------------


def normalize_savepath(value: str | None) -> str:
    """任务 savepath 无前导 /，适配器目录路径统一带前导 /。"""
    raw = str(value or "").strip()
    if not raw or raw == "/":
        return "/"
    path = re.sub(r"/{2,}", "/", "/" + raw.lstrip("/"))
    return path.rstrip("/") or "/"


def _resolve_task_drive(db: Session, task: Task) -> tuple[str, DriveAccount | None]:
    account_name = str(getattr(task, "account_name", "") or "").strip()
    if account_name:
        account = get_active_account(db, account_name)
        if account is not None and str(account.drive_type or ""):
            return str(account.drive_type), account
    drive_type = AdapterFactory.get_drive_type_by_url(str(getattr(task, "shareurl", "") or ""))
    return str(drive_type or ""), None


def preview_token_ttl_seconds() -> int:
    try:
        ttl = int(settings.drama_lead_in_preview_token_ttl_seconds)
    except Exception:
        ttl = 600
    return max(30, min(ttl, 3600))


def _token_secret() -> bytes:
    return str(settings.jwt_secret_key or settings.app_name or "cloud-save-max").encode("utf-8")


def create_preview_token(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    signature = hmac.new(_token_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"v1.{body}.{signature}"


def parse_preview_token(token: str, *, now: float | None = None) -> dict[str, Any] | None:
    if not isinstance(token, str):
        return None
    parts = token.strip().split(".")
    if len(parts) != 3 or parts[0] != "v1" or not parts[1] or not parts[2]:
        return None
    body = parts[1]
    expected = hmac.new(_token_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, parts[2]):
        return None
    padded = body + "=" * (-len(body) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    try:
        expires_at = int(payload.get("exp") or 0)
    except (TypeError, ValueError):
        return None
    current = time.time() if now is None else float(now)
    if expires_at <= current:
        return None
    return payload


def build_task_lead_in_preview(db: Session, task: Task) -> dict[str, Any]:
    """按契约第 3.2 节的顺序生成预览；任何一步不可用都返回 available=False + 中文原因。"""
    task_id = int(task.id)
    task_savepath = str(getattr(task, "savepath", "") or "")
    drive_type, account = _resolve_task_drive(db, task)
    capability = _drive_capability(drive_type) if drive_type else None
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
    if not get_drama_lead_in_enabled(db):
        result["reason"] = "引流功能已关闭"
        return result
    material_config = get_drama_lead_in_material(db, drive_type)
    if material_config is None:
        result["reason"] = "该网盘未配置引流素材"
        return result
    mode = material_mode(material_config)
    if mode == MATERIAL_MODE_OWN_FILE and not capability["own_file_supported"]:
        result["reason"] = f"{capability['drive_name']}暂不支持本账号文件引流"
        return result
    if account is None or str(account.drive_type or "") != drive_type:
        account = _default_account(db, drive_type)
    if account is None:
        result["reason"] = "网盘账号不可用"
        return result
    account_name = str(account.name or "")
    if mode == MATERIAL_MODE_OWN_FILE:
        material_account = str(material_config.get("account_name") or "")
        if material_account != account_name:
            result["reason"] = (
                f"引流素材属于账号「{material_account}」，与任务使用的账号「{account_name}」不一致，"
                "无法内部复制；请改用分享链接素材"
            )
            return result
    profile = resolve_drive_account_profile(account) or {}
    result["account"] = {"account_name": account_name, "nickname": str(profile.get("nickname") or "")}
    dir_path = normalize_savepath(task_savepath)
    adapter = _build_adapter(account)
    if adapter is None:
        result["reason"] = "网盘账号不可用"
        return result
    # 读取到此结束：网盘调用期间不持有数据库事务。
    end_read_tx(db)
    if not _init_adapter(adapter):
        result["reason"] = "网盘账号不可用"
        return result

    dir_response = _safe_call(adapter.lead_in_resolve_dir, dir_path)
    if not _call_ok(dir_response):
        result["reason"] = _directory_failure_reason(dir_response)
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
    if mode == MATERIAL_MODE_OWN_FILE:
        own_path = str(material_config.get("file_path") or "")
        resolver = getattr(adapter, "lead_in_resolve_own_file", None)
        if resolver is None:
            result["reason"] = f"{capability['drive_name']}暂不支持本账号文件引流"
            return result
        own_response = _safe_call(resolver, own_path)
        if not _call_ok(own_response):
            result["reason"] = str(own_response.get("message") or "素材文件不存在，请检查路径")
            return result
        file_info = _own_file_data(own_response)
        if file_info is None or not file_info["name"]:
            result["reason"] = "素材文件不存在，请检查路径"
            return result
        material_fid = str(file_info.get("fid") or material_config.get("file_fid") or "")
        material_name = str(file_info.get("name") or material_config.get("file_name") or "")
        if not material_fid:
            result["reason"] = "引流素材不可用"
            return result
        material = {
            "source": MATERIAL_MODE_OWN_FILE,
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
            "source": MATERIAL_MODE_OWN_FILE,
            "account_name": account_name,
            "path": str(material.get("path") or own_path),
        }
        # 令牌里的名称/路径必须与保存的配置一致，确认时据此判断配置是否变化。
        token_material_name = str(material_config.get("file_name") or material_name)
        token_material_path = own_path
        token_material_size = file_info.get("size")
    else:
        material_response = _safe_call(
            adapter.lead_in_resolve_material,
            str(material_config.get("shareurl") or ""),
            str(material_config.get("passcode") or ""),
            str(material_config.get("file_fid") or ""),
        )
        if not _call_ok(material_response):
            result["reason"] = str(material_response.get("message") or "引流素材不可用")
            return result
        material_payload = material_response.get("data") or {}
        resolved_material = material_payload.get("material") if isinstance(material_payload, dict) else None
        material = dict(resolved_material) if isinstance(resolved_material, dict) else {}
        material_fid = str(material.get("fid") or material_config.get("file_fid") or "")
        material_name = str(material.get("name") or material_config.get("file_name") or "")
        if not material_fid:
            result["reason"] = "引流素材不可用"
            return result
        material["source"] = MATERIAL_MODE_SHARE
        material["fid"] = material_fid
        material["name"] = material_name
        result["material"] = {"name": material_name, "fid": material_fid}
        token_material_name = material_name
        token_material_path = ""
        raw_size = material.get("size")
        token_material_size = raw_size if isinstance(raw_size, int) and not isinstance(raw_size, bool) else None

    inspect_response = _safe_call(adapter.lead_in_inspect, material, target)
    if not _call_ok(inspect_response):
        result["reason"] = str(inspect_response.get("message") or "查重失败，请稍后重试")
        return result
    inspect_data = inspect_response.get("data") or {}
    inspect_data = inspect_data if isinstance(inspect_data, dict) else {}
    result["state"] = str(inspect_data.get("state") or "absent")
    result["state_message"] = str(inspect_data.get("message") or "")

    record = get_lead_in_record(db, task_id, drive_type)
    if result["state"] == "absent":
        # 目标目录里已经查不到该文件（例如用户在网盘手动删除）：清掉过期记录，
        # 让任务列表的「引流」列回到「—」，最近添加时间也一并消失。
        if record is not None:
            db.delete(record)
            db.commit()
    elif record is not None and record.status == "added":
        result["last_added_at"] = normalize_api_datetime(record.updated_at)

    ttl = preview_token_ttl_seconds()
    expires_at = datetime.now(BEIJING_TZ) + timedelta(seconds=ttl)
    result["preview_token"] = create_preview_token(
        {
            "task_id": task_id,
            "drive_type": drive_type,
            "account_name": account_name,
            "dir_path": dir_path,
            "dir_fid": dir_fid,
            "material_fid": material_fid,
            "material_name": token_material_name,
            "material_mode": mode,
            "material_account": str(material_config.get("account_name") or "")
            if mode == MATERIAL_MODE_OWN_FILE
            else "",
            "material_path": token_material_path,
            "material_size": token_material_size,
            "config_version": int(material_config.get("config_version") or 0),
            "exp": int(expires_at.timestamp()),
        }
    )
    result["expires_at"] = expires_at
    result["available"] = True
    return result


# ---------------------------------------------------------------------------
# 确认写入
# ---------------------------------------------------------------------------


def confirm_task_lead_in(
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
    payload = parse_preview_token(preview_token)
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
    if selected_mode not in MATERIAL_MODES:
        raise _stale_preview()
    if selected_mode == MATERIAL_MODE_OWN_FILE and (
        not material_account or not material_path or not material_name
    ):
        raise _stale_preview()

    capability = _drive_capability(drive_type)
    if capability is None or not capability["supported"]:
        raise _stale_preview()
    if selected_mode == MATERIAL_MODE_OWN_FILE and not capability["own_file_supported"]:
        raise _stale_preview()
    if not get_drama_lead_in_enabled(db):
        raise _stale_preview()
    if normalize_savepath(task_savepath) != dir_path:
        raise _stale_preview()
    material_config = get_drama_lead_in_material(db, drive_type)
    if material_config is None:
        raise _stale_preview()
    if int(material_config.get("config_version") or 0) != config_version:
        raise _stale_preview()
    if material_mode(material_config) != selected_mode:
        raise _stale_preview()
    if selected_mode == MATERIAL_MODE_OWN_FILE:
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
    if selected_mode == MATERIAL_MODE_OWN_FILE and material_account != account_name:
        raise _stale_preview()
    current_task_account = str(getattr(task, "account_name", "") or "").strip()
    if current_task_account:
        task_account = get_active_account(db, current_task_account)
        if task_account is not None and str(task_account.drive_type or "") != drive_type:
            raise _stale_preview()

    lock = lead_in_lock(drive_type, account_name, dir_path, material_name)
    if not lock.acquire(blocking=False):
        raise ApiError(code="LEAD_IN_IN_PROGRESS", message="该目录正在添加同名引流文件，请稍后再试", http_status=409)
    try:
        adapter = _build_adapter(account)
        if adapter is None:
            return _store_result(
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
            return _store_result(
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
    if not _init_adapter(adapter):
        return finish("failed", "网盘账号不可用")

    dir_response = _safe_call(adapter.lead_in_resolve_dir, dir_path)
    if not _call_ok(dir_response):
        return finish("failed", _directory_failure_reason(dir_response))
    target = dir_response.get("data") or {}
    target = target if isinstance(target, dict) else {}

    material: dict[str, Any]
    if material_mode == MATERIAL_MODE_OWN_FILE:
        file_path = str(material_config.get("file_path") or "")
        resolver = getattr(adapter, "lead_in_resolve_own_file", None)
        if resolver is None:
            return finish("failed", "该网盘暂不支持本账号文件引流")
        own_response = _safe_call(resolver, file_path)
        if not _call_ok(own_response):
            return finish("failed", str(own_response.get("message") or "素材文件不存在，请检查路径"))
        file_info = _own_file_data(own_response)
        if file_info is None or not file_info["name"]:
            return finish("failed", "素材文件不存在，请检查路径")
        material = {
            "source": MATERIAL_MODE_OWN_FILE,
            "fid": str(file_info.get("fid") or material_fid),
            "name": str(file_info.get("name") or material_name),
            "path": str(file_info.get("path") or file_path),
            "size": file_info.get("size"),
            "md5": str(file_info.get("md5") or ""),
            "account_name": str(material_config.get("account_name") or ""),
            "dir": False,
        }
    else:
        material_response = _safe_call(
            adapter.lead_in_resolve_material,
            str(material_config.get("shareurl") or ""),
            str(material_config.get("passcode") or ""),
            material_fid,
        )
        if not _call_ok(material_response):
            return finish("failed", str(material_response.get("message") or "引流素材不可用"))
        material_payload = material_response.get("data") or {}
        resolved_material = material_payload.get("material") if isinstance(material_payload, dict) else None
        material = dict(resolved_material) if isinstance(resolved_material, dict) else {}
        material.setdefault("fid", material_fid)
        material.setdefault("name", material_name)
        material["source"] = MATERIAL_MODE_SHARE

    inspect_response = _safe_call(adapter.lead_in_inspect, material, target)
    if not _call_ok(inspect_response):
        return finish("failed", str(inspect_response.get("message") or "查重失败，请稍后重试"))
    inspect_data = inspect_response.get("data") or {}
    inspect_data = inspect_data if isinstance(inspect_data, dict) else {}
    state = str(inspect_data.get("state") or "absent")
    if state == "exists":
        return finish("exists", str(inspect_data.get("message") or "目标目录已存在该文件，无需重复添加"))
    if state == "conflict":
        return finish("conflict", str(inspect_data.get("message") or "目标目录存在同名文件，未做任何覆盖"))

    if material_mode == MATERIAL_MODE_OWN_FILE:
        add_method = getattr(adapter, "lead_in_add_own_file", None)
        if add_method is None:
            return finish("failed", "该网盘暂不支持本账号文件引流")
    else:
        add_method = adapter.lead_in_add

    # 从这里开始向网盘提交写请求：之后任何异常/超时都只能报 pending，绝不谎报 added。
    add_response = _safe_call(add_method, material, target)
    if not _call_ok(add_response):
        if _is_pending_response(add_response) or _error_code(add_response) == "LEAD_IN_ADAPTER_ERROR":
            return finish("pending", _PENDING_MESSAGE)
        return finish("failed", str(add_response.get("message") or "添加引流文件失败"))
    add_payload = add_response.get("data") or {}
    handle = add_payload.get("handle") if isinstance(add_payload, dict) else None

    verify_response = _safe_call(adapter.lead_in_verify, material, target, handle)
    if not _call_ok(verify_response):
        if _is_pending_response(verify_response) or _error_code(verify_response) == "LEAD_IN_ADAPTER_ERROR":
            return finish("pending", _PENDING_MESSAGE)
        return finish("failed", str(verify_response.get("message") or "核验添加结果失败"))
    verify_payload = verify_response.get("data") or {}
    verify_payload = verify_payload if isinstance(verify_payload, dict) else {}
    verify_state = str(verify_payload.get("state") or "pending")
    if verify_state == "added":
        return finish("added", str(verify_payload.get("message") or "引流文件已添加"))
    if verify_state == "pending":
        return finish("pending", str(verify_payload.get("message") or _PENDING_MESSAGE))
    return finish("failed", str(verify_payload.get("message") or "添加引流文件失败"))


def _stale_preview() -> ApiError:
    return ApiError(code="LEAD_IN_STALE_PREVIEW", message="预览内容已变化，请重新预览", http_status=409)


def _store_result(
    db: Session,
    *,
    task_id: int,
    drive_type: str,
    account_name: str,
    dir_path: str,
    material_name: str,
    material_fid: str,
    status: str,
    message: str,
    config_version: int,
    actor_user_id: int | None,
    ip: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    status = status if status in _ACTIVE_STATUSES else "failed"
    record: TaskLeadInRecord | None = None
    try:
        record = _upsert_record(
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
        )
        detail = json.dumps(
            {
                "drive_type": drive_type,
                "account_name": account_name,
                "dir_path": dir_path,
                "material_name": material_name,
                "status": status,
                "message": message,
            },
            ensure_ascii=False,
        )[:1024]
        write_audit_log(
            db,
            actor_user_id=actor_user_id,
            action="task.lead_in_add",
            target_type="task",
            target_id=str(task_id),
            ip=ip,
            user_agent=user_agent,
            success=status in ("added", "exists"),
            detail=detail,
        )
        db.commit()
        db.refresh(record)
    except Exception:
        db.rollback()
        logger.warning("保存引流记录失败: task_id=%s drive_type=%s", task_id, drive_type, exc_info=True)
        record = None

    return {
        "status": status,
        "message": str(message or ""),
        "added_at": normalize_api_datetime(record.updated_at) if record is not None and status == "added" else None,
        "record_id": int(record.id) if record is not None else 0,
        "material": {"name": material_name, "fid": material_fid},
        "directory": {"path": dir_path, "fid": "", "exists": True},
    }


def _upsert_record(
    db: Session,
    *,
    task_id: int,
    drive_type: str,
    account_name: str,
    dir_path: str,
    material_name: str,
    material_fid: str,
    status: str,
    message: str,
    config_version: int,
) -> TaskLeadInRecord:
    record = (
        db.execute(
            select(TaskLeadInRecord).where(
                TaskLeadInRecord.task_id == task_id,
                TaskLeadInRecord.drive_type == drive_type,
            )
        )
        .scalars()
        .first()
    )
    if record is None:
        record = TaskLeadInRecord(task_id=task_id, drive_type=drive_type)
        db.add(record)
    record.account_name = (account_name or "")[:128]
    record.dir_path = (dir_path or "")[:512]
    record.material_name = (material_name or "")[:512]
    record.material_fid = (material_fid or "")[:256]
    record.status = status
    record.message = (message or "")[:512]
    record.config_version = int(config_version or 0)
    db.flush()
    return record


# ---------------------------------------------------------------------------
# 记录查询（任务列表批量填充禁止 N+1）
# ---------------------------------------------------------------------------


def get_lead_in_record(db: Session, task_id: int, drive_type: str | None = None) -> TaskLeadInRecord | None:
    try:
        normalized_task_id = int(task_id or 0)
    except (TypeError, ValueError):
        return None
    if normalized_task_id <= 0:
        return None
    statement = select(TaskLeadInRecord).where(TaskLeadInRecord.task_id == normalized_task_id)
    if drive_type:
        statement = statement.where(TaskLeadInRecord.drive_type == str(drive_type))
    return db.execute(
        statement.order_by(TaskLeadInRecord.updated_at.desc(), TaskLeadInRecord.id.desc())
    ).scalars().first()


def get_task_lead_in_state(db: Session, task: Task) -> dict[str, Any]:
    """任务详情里的引流记录摘要，无记录时 state="none"。"""
    try:
        task_id = int(getattr(task, "id", 0) or 0)
    except (TypeError, ValueError):
        task_id = 0
    if task_id <= 0:
        return {"state": "none", "message": "", "last_added_at": None, "material_name": ""}
    drive_type, _ = _resolve_task_drive(db, task)
    record = get_lead_in_record(db, task_id, drive_type)
    if record is None:
        record = get_lead_in_record(db, task_id)
    if record is None:
        return {"state": "none", "message": "", "last_added_at": None, "material_name": ""}
    status = record.status if record.status in _ACTIVE_STATUSES else "failed"
    return {
        "state": status,
        "message": str(record.message or ""),
        "last_added_at": normalize_api_datetime(record.updated_at) if status == "added" else None,
        "material_name": str(record.material_name or ""),
    }


def _record_sort_key(record: TaskLeadInRecord) -> tuple[datetime, int]:
    updated = record.updated_at
    if updated is None:
        updated = datetime.min
    elif updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return (updated, int(record.id or 0))


def load_lead_in_summary_map(db: Session, task_ids: list[int]) -> dict[int, dict[str, Any]]:
    """一次 IN 查询批量取每个任务最近的引流记录，供任务列表填充 lead_in 字段。"""
    normalized: list[int] = []
    for task_id in task_ids:
        try:
            value = int(task_id)
        except (TypeError, ValueError):
            continue
        if value > 0 and value not in normalized:
            normalized.append(value)
    summary: dict[int, dict[str, Any]] = {
        task_id: {"state": "none", "last_added_at": None} for task_id in normalized
    }
    if not normalized:
        return summary
    records = (
        db.execute(select(TaskLeadInRecord).where(TaskLeadInRecord.task_id.in_(normalized)))
        .scalars()
        .all()
    )
    latest: dict[int, TaskLeadInRecord] = {}
    for record in records:
        task_id = int(record.task_id)
        previous = latest.get(task_id)
        if previous is None or _record_sort_key(record) > _record_sort_key(previous):
            latest[task_id] = record
    for task_id, record in latest.items():
        status = record.status if record.status in _ACTIVE_STATUSES else "failed"
        summary[task_id] = {
            "state": status,
            "last_added_at": normalize_api_datetime(record.updated_at) if status == "added" else None,
        }
    return summary


def lead_in_lock(drive_type: str, account_name: str, dir_path: str, material_name: str) -> threading.Lock:
    """进程内并发锁：(网盘, 账号, 目录, 素材名) 维度，非阻塞获取。

    use_keyed_locks=True（默认）走 LRUKeyedLocks（上限 1024，淘汰未持有的锁）；
    置 False 回退到旧的无限 dict 实现。单进程假设：多 worker 时跨进程不互斥。
    """
    key = (str(drive_type or ""), str(account_name or ""), str(dir_path or ""), str(material_name or ""))
    if settings.use_keyed_locks:
        return _LEAD_IN_KEYED_LOCKS.get(key)
    with _LEAD_IN_LOCK_GUARD:
        lock = _LEAD_IN_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LEAD_IN_LOCKS[key] = lock
        return lock
