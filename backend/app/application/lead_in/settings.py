from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import bad_request
from app.core.runtime.tx import end_read_tx
from app.application.container import system_settings_repo
from app.application.drive_accounts import BEIJING_TZ, get_active_account

logger = logging.getLogger(__name__)

import app.application.lead_in.materials as lead_materials


def _get_setting(db: Session, key: str, default: str = "") -> str:
    return system_settings_repo().get_value(db, key, default)


def _set_setting(db: Session, key: str, value: str, description: str = "") -> None:
    system_settings_repo().set_value(db, key, value, description)


def get_drama_lead_in_enabled(db: Session) -> bool:
    return _get_setting(db, lead_materials.SETTING_KEY_ENABLED, "0").strip() == "1"


def get_drama_lead_in_materials(db: Session) -> dict[str, dict[str, Any]]:
    raw = _get_setting(db, lead_materials.SETTING_KEY_MATERIALS, "{}")
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


def get_drama_lead_in_material(db: Session, drive_type: str) -> dict[str, Any] | None:
    """取某网盘已保存且可用的素材配置；未配置或缺少必需字段时返回 None。"""
    item = get_drama_lead_in_materials(db).get(str(drive_type or ""))
    if not isinstance(item, dict):
        return None
    mode = lead_materials.material_mode(item)
    if mode == lead_materials.MATERIAL_MODE_OWN_FILE:
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


def build_settings_payload(db: Session) -> dict[str, Any]:
    enabled = get_drama_lead_in_enabled(db)
    materials = get_drama_lead_in_materials(db)
    drives: list[dict[str, Any]] = []
    for capability in lead_materials.list_drive_capabilities():
        drive_type = capability["drive_type"]
        item = materials.get(drive_type)
        item = item if isinstance(item, dict) else {}
        mode = lead_materials.material_mode(item)
        shareurl = str(item.get("shareurl") or "")
        account_name = str(item.get("account_name") or "")
        file_path = str(item.get("file_path") or "")
        file_name = str(item.get("file_name") or "")
        raw_size = item.get("file_size")
        file_size = (
            raw_size
            if isinstance(raw_size, int) and not isinstance(raw_size, bool)
            else None
        )
        if mode == lead_materials.MATERIAL_MODE_OWN_FILE:
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
                "shareurl": lead_materials.mask_shareurl(shareurl),
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
    return {
        "enabled": enabled,
        "version": lead_materials.SETTINGS_VERSION,
        "drives": drives,
    }


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
        capability = lead_materials._drive_capability(drive_type)
        if capability is None:
            raise bad_request("LEAD_IN_UNSUPPORTED", "未知的网盘类型")
        if not capability["supported"]:
            raise bad_request(
                "LEAD_IN_UNSUPPORTED", capability["reason"] or "该网盘暂不支持引流"
            )
        mode = (
            str(raw.get("mode") or lead_materials.MATERIAL_MODE_SHARE).strip()
            or lead_materials.MATERIAL_MODE_SHARE
        )
        if mode not in lead_materials.MATERIAL_MODES:
            raise bad_request("LEAD_IN_INVALID_MODE", "不支持的素材来源")
        if (
            mode == lead_materials.MATERIAL_MODE_OWN_FILE
            and not capability["own_file_supported"]
        ):
            raise bad_request(
                "LEAD_IN_UNSUPPORTED",
                f"{capability['drive_name']}暂不支持本账号文件引流",
            )
        existing = materials.get(drive_type)
        existing = existing if isinstance(existing, dict) else {}
        shareurl = str(raw.get("shareurl") or "").strip()
        account_name = str(raw.get("account_name") or "").strip()
        file_path = str(raw.get("file_path") or "").strip()
        if mode == lead_materials.MATERIAL_MODE_OWN_FILE:
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
        if item["mode"] == lead_materials.MATERIAL_MODE_OWN_FILE:
            account = get_active_account(db, item["account_name"])
            if account is None or str(account.drive_type or "") != drive_type:
                raise bad_request(
                    "LEAD_IN_ACCOUNT_UNAVAILABLE",
                    "网盘账号不可用，请先在网盘账号页添加该网盘账号",
                )
            account = lead_materials.account_snapshot(account)
            end_read_tx(db)
            adapter = lead_materials._build_adapter(account)
            if adapter is None:
                raise bad_request(
                    "LEAD_IN_ACCOUNT_UNAVAILABLE", "网盘账号不可用，请检查账号配置"
                )
            end_read_tx(db)
            if not lead_materials._init_adapter(adapter):
                raise bad_request(
                    "LEAD_IN_ACCOUNT_UNAVAILABLE",
                    "网盘账号不可用（登录失败），请检查账号状态",
                )
            resolver = getattr(adapter, "lead_in_resolve_own_file", None)
            if resolver is None:
                raise bad_request("LEAD_IN_UNSUPPORTED", "该网盘暂不支持本账号文件引流")
            response = lead_materials._safe_call(resolver, item["file_path"])
            if not lead_materials._call_ok(response):
                raise lead_materials._lead_in_error(
                    response, "LEAD_IN_MATERIAL_RESOLVE_FAILED", "素材文件解析失败"
                )
            file_info = lead_materials._own_file_data(response)
            if file_info is None or not file_info["name"]:
                raise bad_request(
                    "LEAD_IN_MATERIAL_UNAVAILABLE", "素材文件不存在，请检查路径"
                )
            resolved.append({**item, "own_file": file_info})
            continue

        account = lead_materials._default_account(db, drive_type)
        if account is None:
            raise bad_request(
                "LEAD_IN_ACCOUNT_UNAVAILABLE",
                "网盘账号不可用，请先在网盘账号页添加该网盘账号",
            )
        account = lead_materials.account_snapshot(account)
        end_read_tx(db)
        adapter = lead_materials._build_adapter(account)
        if adapter is None:
            raise bad_request(
                "LEAD_IN_ACCOUNT_UNAVAILABLE", "网盘账号不可用，请检查账号配置"
            )
        end_read_tx(db)
        if not lead_materials._init_adapter(adapter):
            raise bad_request(
                "LEAD_IN_ACCOUNT_UNAVAILABLE",
                "网盘账号不可用（登录失败），请检查账号状态",
            )
        response = lead_materials._safe_call(
            adapter.lead_in_resolve_material,
            item["shareurl"],
            item["passcode"],
            item["file_fid"],
        )
        if not lead_materials._call_ok(response):
            code = (
                lead_materials._error_code(response)
                or "LEAD_IN_MATERIAL_RESOLVE_FAILED"
            )
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
        if item["mode"] == lead_materials.MATERIAL_MODE_OWN_FILE:
            file_info = item.get("own_file") or {}
            materials[drive_type] = {
                "mode": lead_materials.MATERIAL_MODE_OWN_FILE,
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
            raise bad_request(
                "LEAD_IN_MATERIAL_RESOLVE_FAILED",
                "未能定位到唯一的素材文件，请先扫描并选择",
            )
        materials[drive_type] = {
            "mode": lead_materials.MATERIAL_MODE_SHARE,
            "shareurl": item["shareurl"],
            "passcode": item["passcode"],
            "file_fid": material_fid,
            "file_name": item.get("material_name") or "",
            "config_version": config_version,
            "updated_at": updated_at,
        }

    _set_setting(
        db,
        lead_materials.SETTING_KEY_ENABLED,
        "1" if enabled else "0",
        lead_materials.SETTING_DESCRIPTION_ENABLED,
    )
    _set_setting(
        db,
        lead_materials.SETTING_KEY_MATERIALS,
        json.dumps(materials, ensure_ascii=False),
        lead_materials.SETTING_DESCRIPTION_MATERIALS,
    )
    # 提交归属：写设置 + 审计由路由的事务边界统一提交（原实现在此提前 commit）。
    db.flush()
    return build_settings_payload(db)
