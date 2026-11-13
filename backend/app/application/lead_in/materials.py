from __future__ import annotations

import logging
import re
from typing import Any, Callable
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.core.errors import ApiError, bad_request
from app.core.runtime.tx import end_read_tx
from app.application.container import adapter_factory as _acc_AdapterFactory

AdapterFactory = _acc_AdapterFactory()
from app.application.drive_accounts import get_active_account, pick_default_account_name

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


def material_mode(item: dict[str, Any] | None) -> str:
    """素材模式：历史配置没有 mode 字段时一律按 share 处理。"""
    if not isinstance(item, dict):
        return MATERIAL_MODE_SHARE
    mode = str(item.get("mode") or "").strip()
    return mode if mode in MATERIAL_MODES else MATERIAL_MODE_SHARE


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


def _drive_capability(drive_type: str) -> dict[str, Any] | None:
    adapter_class = AdapterFactory.ADAPTER_MAP.get(str(drive_type or ""))
    if adapter_class is None:
        return None
    drive_name = str(getattr(adapter_class, "DRIVE_NAME", "") or drive_type)
    supported = bool(getattr(adapter_class, "SUPPORTS_DRAMA_LEAD_IN", False))
    own_file_supported = bool(
        getattr(adapter_class, "SUPPORTS_LEAD_IN_OWN_FILE", False)
    )
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
        for capability in (
            _drive_capability(drive_type)
            for drive_type in AdapterFactory.ADAPTER_MAP.keys()
        )
        if capability is not None
    ]


def scan_drama_lead_in_material(
    db: Session, drive_type: str, shareurl: str, passcode: str
) -> dict[str, Any]:
    """列出分享内文件供前端选择素材；适配器判定多文件歧义但返回 files 时按成功处理。"""
    drive_type = str(drive_type or "").strip()
    capability = _drive_capability(drive_type)
    if capability is None:
        raise bad_request("LEAD_IN_UNSUPPORTED", "未知的网盘类型")
    if not capability["supported"]:
        raise bad_request(
            "LEAD_IN_UNSUPPORTED", capability["reason"] or "该网盘暂不支持引流"
        )
    shareurl = str(shareurl or "").strip()
    if not shareurl:
        raise bad_request("LEAD_IN_SHAREURL_REQUIRED", "请填写素材分享链接")
    account = _default_account(db, drive_type)
    if account is None:
        raise bad_request(
            "LEAD_IN_ACCOUNT_UNAVAILABLE",
            "网盘账号不可用，请先在网盘账号页添加该网盘账号",
        )
    account = account_snapshot(account)
    end_read_tx(db)
    adapter = _build_adapter(account)
    if adapter is None:
        raise bad_request(
            "LEAD_IN_ACCOUNT_UNAVAILABLE", "网盘账号不可用，请检查账号配置"
        )
    end_read_tx(db)
    if not _init_adapter(adapter):
        raise bad_request(
            "LEAD_IN_ACCOUNT_UNAVAILABLE", "网盘账号不可用（登录失败），请检查账号状态"
        )
    response = _safe_call(
        adapter.lead_in_resolve_material, shareurl, str(passcode or ""), ""
    )
    payload = response.get("data") or {}
    files = _normalize_scan_files(
        payload.get("files") if isinstance(payload, dict) else None
    )
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
        raise _lead_in_error(
            response, "LEAD_IN_MATERIAL_RESOLVE_FAILED", "素材文件解析失败"
        )
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
        raise _lead_in_error(
            response, "LEAD_IN_DIR_LIST_FAILED", "读取目录失败，请稍后重试"
        )
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    return {
        "path": str(data.get("path") or path),
        "fid": str(data.get("fid") or ""),
        "entries": _normalize_dir_entries(data.get("entries")),
    }


def _default_account(db: Session, drive_type: str) -> Any:
    account_name = pick_default_account_name(db, drive_type)
    if not account_name:
        return None
    return get_active_account(db, account_name)


def _build_adapter(account):
    from app.application.container import lead_in_provider

    return lead_in_provider().create(account)


def _init_adapter(adapter) -> bool:
    if adapter is None:
        return False
    try:
        adapter.ensure_initialized(ttl_seconds=AdapterFactory.init_ttl_seconds())
        return True
    except Exception:
        logger.warning("引流适配器初始化失败")
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
        raise bad_request(
            "LEAD_IN_UNSUPPORTED", capability["reason"] or "该网盘暂不支持引流"
        )
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
        raise bad_request(
            "LEAD_IN_ACCOUNT_UNAVAILABLE",
            "网盘账号不可用，请先在网盘账号页添加该网盘账号",
        )
    account = account_snapshot(account)
    end_read_tx(db)
    adapter = _build_adapter(account)
    if adapter is None:
        raise bad_request(
            "LEAD_IN_ACCOUNT_UNAVAILABLE", "网盘账号不可用，请检查账号配置"
        )
    end_read_tx(db)
    if not _init_adapter(adapter):
        raise bad_request(
            "LEAD_IN_ACCOUNT_UNAVAILABLE", "网盘账号不可用（登录失败），请检查账号状态"
        )
    return adapter, capability, path


def _safe_call(func: Callable[..., Any], *args) -> dict[str, Any]:
    try:
        response = func(*args)
    except Exception as exc:
        return {
            "code": (
                "LEAD_IN_TIMEOUT" if _is_timeout_error(exc) else "LEAD_IN_ADAPTER_ERROR"
            ),
            "message": _friendly_error(exc),
            "data": None,
        }
    if not isinstance(response, dict):
        return {
            "code": "LEAD_IN_ADAPTER_ERROR",
            "message": "网盘响应异常，请稍后重试",
            "data": None,
        }
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
    return (
        "超时" in message
        or "timeout" in lowered
        or "unknown" in lowered
        and "pending" in lowered
    )


def _directory_failure_reason(response: Any) -> str:
    code = _error_code(response)
    if code in ("LEAD_IN_DIR_NOT_FOUND", "LEAD_IN_DIR_INVALID"):
        return "任务保存目录不存在"
    message = (
        str(response.get("message") or "").strip() if isinstance(response, dict) else ""
    )
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
                "size": (
                    size
                    if isinstance(size, int) and not isinstance(size, bool)
                    else None
                ),
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
                "size": (
                    size
                    if isinstance(size, int) and not isinstance(size, bool)
                    else None
                ),
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
        "size": (
            raw_size
            if isinstance(raw_size, int) and not isinstance(raw_size, bool)
            else None
        ),
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
    message = (
        str(response.get("message") or default_message)
        if isinstance(response, dict)
        else default_message
    )
    if not code.startswith("LEAD_IN_"):
        code = default_code
    return bad_request(code, message)


def normalize_savepath(value: str | None) -> str:
    """任务 savepath 无前导 /，适配器目录路径统一带前导 /。"""
    raw = str(value or "").strip()
    if not raw or raw == "/":
        return "/"
    path = re.sub(r"/{2,}", "/", "/" + raw.lstrip("/"))
    return path.rstrip("/") or "/"


def _resolve_task_drive(db: Session, task: Any) -> tuple[str, Any]:
    account_name = str(getattr(task, "account_name", "") or "").strip()
    if account_name:
        account = get_active_account(db, account_name)
        if account is not None and str(account.drive_type or ""):
            return str(account.drive_type), account
    drive_type = AdapterFactory.get_drive_type_by_url(
        str(getattr(task, "shareurl", "") or "")
    )
    return str(drive_type or ""), None


from dataclasses import dataclass


@dataclass(frozen=True)
class LeadInAccount:
    name: str
    drive_type: str
    cookie: str
    config_json: str


def account_snapshot(account):
    return LeadInAccount(
        str(account.name),
        str(account.drive_type),
        str(getattr(account, "cookie", "") or ""),
        str(getattr(account, "config_json", "") or ""),
    )
