"""Shared listing wire values and pure response rules; no adapter dependencies."""
import json
from typing import Any

ADRIVE_V3_FILE_LIST = "/adrive/v3/file/list"
XUNLEI_API_BASE = "https://api-pan.xunlei.com"
FILE_LIST_FILTERS = json.dumps({
    "phase": {"eq": "PHASE_TYPE_COMPLETE"},
    "trashed": {"eq": False},
})


def guangya_message(payload: Any, default: str = "") -> str:
    if isinstance(payload, dict):
        for key in ("message", "msg", "errorMessage", "error_message", "detail"):
            value = payload.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
    return default


def guangya_success(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return bool(payload)
    if payload.get("success") is True:
        return True
    if payload.get("ok") is True:
        return True
    for key in ("msg", "message"):
        value = payload.get(key)
        if str(value or "").strip().lower() == "success":
            return True
    for key in ("code", "status", "result"):
        if key not in payload:
            continue
        value = payload.get(key)
        if isinstance(value, (int, float)) and int(value) in (0, 200):
            return True
        if str(value).strip().lower() in {"0", "200", "ok", "success"}:
            return True
    if "data" in payload and payload.get("data") is not None and not guangya_message(payload):
        return True
    return False
