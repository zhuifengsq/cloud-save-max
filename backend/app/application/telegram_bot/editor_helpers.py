from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any
from app.application.telegram_bot.actions import TelegramBotActions


TASK_FIELDS: list[dict[str, Any]] = [
    {"key": "task_type", "label": "任务类型", "type": "enum", "options": ["drama"], "default": "drama", "editor": False},
    {"key": "taskname", "label": "任务名称", "type": "str", "default": ""},
    {"key": "shareurl", "label": "分享链接", "type": "str", "default": ""},
    {"key": "__share_folder__", "label": "分享目录", "type": "action", "default": "", "display_key": "__share_folder_label__", "transient": True},
    {"key": "savepath", "label": "保存路径", "type": "str", "default": ""},
    {"key": "account_name", "label": "执行账号", "type": "str", "default": ""},
    {"key": "__magic_rule__", "label": "内置规则", "type": "action", "default": "", "display_key": "__magic_rule_label__", "transient": True},
    {"key": "pattern", "label": "匹配规则", "type": "str", "default": ""},
    {"key": "replace", "label": "替换规则", "type": "str", "default": ""},
    {"key": "__tmdb_bind__", "label": "TMDB 关联", "type": "action", "default": "", "display_key": "__tmdb_label__", "transient": True},
    {"key": "tmdb_id", "label": "TMDB ID", "type": "int", "default": None, "editor": False},
    {"key": "tmdb_media_type", "label": "TMDB 类型", "type": "enum", "options": ["", "tv", "movie"], "default": "", "editor": False},
    {"key": "enabled", "label": "启用任务", "type": "bool", "default": True, "editor": False},
    {"key": "ignore_extension", "label": "忽略扩展名", "type": "bool", "default": False},
]

ACCOUNT_FIELDS: list[dict[str, Any]] = [
    {"key": "name", "label": "账号名称", "type": "str", "default": ""},
    {"key": "drive_type", "label": "网盘类型", "type": "enum_dynamic", "options_key": "drive_types", "default": ""},
    {"key": "cookie", "label": "Cookie", "type": "str", "default": ""},
    {"key": "config", "label": "配置 JSON", "type": "json", "default": {}},
    {"key": "enabled", "label": "启用账号", "type": "bool", "default": True, "editor": False},
    {"key": "is_default", "label": "默认账号", "type": "bool", "default": False, "editor": False},
    {"key": "capacity_warning_threshold", "label": "容量告警阈值", "type": "int", "default": 85},
]

SETTING_FIELD_MAP: dict[str, dict[str, dict[str, Any]]] = {
    "task_scheduler": {
        "enabled": {"label": "启用任务调度", "type": "bool"},
        "crontab": {"label": "Cron 表达式", "type": "str"},
        "timezone": {"label": "时区", "type": "str"},
    },
    "probe_scheduler": {
        "enabled": {"label": "启用账号探测", "type": "bool"},
        "crontab": {"label": "Cron 表达式", "type": "str"},
        "timezone": {"label": "时区", "type": "str"},
        "enabled_only": {"label": "仅探测启用账号", "type": "bool"},
    },
    "tmdb": {
        "api_key": {"label": "TMDB API Key", "type": "str"},
        "has_api_key": {"label": "已配置 API Key", "type": "bool", "editor": False},
        "language": {"label": "元数据语言", "type": "str"},
        "poster_language": {"label": "海报语言", "type": "str"},
    },
}

NOTIFICATION_FIELD_MAP: dict[str, dict[str, Any]] = {
    "HITOKOTO": {"label": "一言文案", "type": "bool"},
    "TG_BOT_TOKEN": {"label": "Telegram Bot Token", "type": "str"},
    "TG_USER_ID": {"label": "Telegram 用户 ID", "type": "str"},
    "TG_API_HOST": {"label": "Telegram API 地址", "type": "str"},
    "TG_PROXY_AUTH": {"label": "Telegram 代理认证", "type": "str"},
    "TG_PROXY_HOST": {"label": "Telegram 代理地址", "type": "str"},
    "TG_PROXY_PORT": {"label": "Telegram 代理端口", "type": "str"},
    "SMTP_SERVER": {"label": "SMTP 服务器", "type": "str"},
    "SMTP_SSL": {"label": "SMTP SSL", "type": "bool"},
    "SMTP_EMAIL": {"label": "SMTP 发件邮箱", "type": "str"},
    "SMTP_PASSWORD": {"label": "SMTP 密码", "type": "str"},
    "SMTP_NAME": {"label": "SMTP 发件名称", "type": "str"},
    "SMTP_EMAIL_TO": {"label": "SMTP 收件邮箱", "type": "str"},
    "SMTP_NAME_TO": {"label": "SMTP 收件名称", "type": "str"},
    "WEBHOOK_URL": {"label": "Webhook 地址", "type": "str"},
    "WEBHOOK_METHOD": {"label": "Webhook 方法", "type": "str"},
    "WEBHOOK_HEADERS": {"label": "Webhook 请求头", "type": "str"},
    "WEBHOOK_BODY": {"label": "Webhook 请求体", "type": "str"},
    "WEBHOOK_CONTENT_TYPE": {"label": "Webhook Content-Type", "type": "str"},
}

def _field_meta(fields: list[dict[str, Any]], key: str) -> dict[str, Any]:
    for item in fields:
        if item["key"] == key:
            return item
    raise KeyError(key)

def _editor_fields(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [x for x in fields if x.get("editor", True) is not False]

def _pretty_setting_key(key: str) -> str:
    parts = [part for part in str(key).replace(".", " ").split() if part]
    tokens: list[str] = []
    preserve = {"tg", "tmdb", "smtp", "url", "api", "id", "qq"}
    for part in parts:
        for token in str(part).split("_"):
            raw = token.strip()
            if not raw:
                continue
            if raw.lower() in preserve:
                tokens.append(raw.upper())
            elif raw.isupper():
                tokens.append(raw)
            else:
                tokens.append(raw.replace("-", " ").title())
    return " ".join(tokens) or key

def _infer_setting_type(current: Any, field: str) -> str:
    if isinstance(current, bool):
        return "bool"
    if isinstance(current, int) and not isinstance(current, bool):
        return "int"
    if isinstance(current, float):
        return "float"
    lowered = str(field or "").lower()
    if lowered.startswith("has_") or lowered.endswith("_enabled") or lowered == "enabled":
        return "bool"
    return "str"

def _setting_field_meta(domain: str, field: str, current: Any) -> dict[str, Any]:
    if domain == "notifications":
        meta = dict(NOTIFICATION_FIELD_MAP.get(field, {}))
    else:
        meta = dict(SETTING_FIELD_MAP.get(domain, {}).get(field, {}))
    meta.setdefault("key", field)
    meta.setdefault("label", _pretty_setting_key(field))
    meta.setdefault("type", _infer_setting_type(current, field))
    return meta

def _setting_fields(actions: TelegramBotActions, domain: str, values: dict[str, Any], *, page: int = 1) -> tuple[list[dict[str, Any]], int | None, int | None]:
    if domain == "notifications":
        keys = actions.raw_notification_keys()
        page_size = 12
        start = max(0, (page - 1) * page_size)
        fields = [_setting_field_meta(domain, key, values.get(key)) for key in keys[start : start + page_size]]
        return _editor_fields(fields), len(keys), page_size
    fields: list[dict[str, Any]] = []
    for key, value in values.items():
        fields.append(_setting_field_meta(domain, str(key), value))
    return _editor_fields(fields), None, None

def _setting_display_values(domain: str, values: dict[str, Any]) -> dict[str, Any]:
    return dict(values or {})

def _set_nested(target: dict[str, Any], path: str, value: Any) -> None:
    parts = [p for p in str(path).split(".") if p]
    node = target
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value

def _get_nested(target: dict[str, Any], path: str) -> Any:
    node: Any = target
    for part in [p for p in str(path).split(".") if p]:
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node

def _flatten_seed(fields: list[dict[str, Any]], source: dict[str, Any] | None = None) -> dict[str, Any]:
    draft: dict[str, Any] = {}
    source = source or {}
    for item in fields:
        value = _get_nested(source, item["key"])
        if value is None:
            value = item.get("default")
        _set_nested(draft, item["key"], value)
        display_key = str(item.get("display_key") or "")
        if display_key:
            display_value = _get_nested(source, display_key)
            if display_value is None:
                display_value = []
            _set_nested(draft, display_key, display_value)
    return draft

def _collapse_payload(fields: list[dict[str, Any]], draft: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for item in fields:
        if item.get("transient"):
            continue
        key = item["key"]
        value = _get_nested(draft, key)
        if item["type"] == "str" and value == "":
            value = None
        if item["type"] == "enum" and value == "":
            value = None
        _set_nested(payload, key, value)
    return payload

def _parse_scalar(text: str, field_type: str) -> Any:
    raw = str(text or "").strip()
    if field_type == "str":
        return raw
    if field_type == "int":
        if not raw:
            return None
        return int(raw)
    if field_type == "float":
        if not raw:
            return None
        return float(raw)
    if field_type == "bool":
        return raw.lower() in {"1", "true", "yes", "y", "on", "是", "启用"}
    if field_type == "list":
        if not raw:
            return []
        return [x for x in [i.strip() for i in raw.replace("\n", ",").split(",")] if x]
    if field_type == "json":
        if not raw:
            return {}
        import json

        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("JSON 必须是对象")
        return value
    return raw

def _field_prompt(meta: dict[str, Any]) -> str:
    label = str(meta.get("label") or meta.get("key") or "字段")
    field_type = str(meta.get("type") or "str")
    if field_type == "list":
        return f"请输入 {label}，多个值用逗号或换行分隔。留空可清空。"
    if field_type == "json":
        return f"请输入 {label}，内容必须是 JSON 对象。留空可清空。"
    if field_type == "int":
        return f"请输入 {label}，需要是整数。留空可清空。"
    if field_type == "float":
        return f"请输入 {label}，需要是数字。留空可清空。"
    return f"请输入 {label}。留空可清空。"

def _editor_title(kind: str, target_id: int | None = None) -> str:
    if kind == "task":
        return f"编辑任务 #{target_id}" if target_id else "新建任务"
    return f"编辑账号 #{target_id}" if target_id else "新建账号"

def _remote_parent_path(path: str) -> str:
    normalized = str(path or "").strip() or "/"
    normalized = "/" + "/".join([p for p in normalized.split("/") if p])
    if normalized == "/":
        return "/"
    parts = [p for p in normalized.split("/") if p]
    if len(parts) <= 1:
        return "/"
    return "/" + "/".join(parts[:-1])

def _suggest_taskname_from_share_text(text: str, shareurl: str, latest_video_name: str | None = None) -> str:
    candidate = str(text or "").strip()
    if shareurl:
        candidate = candidate.replace(str(shareurl).strip(), " ")
    candidate = re.sub(r"https?://[^\s]+", " ", candidate)
    candidate = re.sub(r"(提取码|访问码|密码|pwd|password)[：:\s#-]*[A-Za-z0-9]{4,8}", " ", candidate, flags=re.IGNORECASE)
    candidate = re.sub(r"[（(][A-Za-z0-9]{4}[)）]", " ", candidate)
    candidate = re.sub(r"\s+", " ", candidate).strip(" -_|,，:：")
    if len(candidate) >= 2:
        return candidate[:120]
    latest_name = str(latest_video_name or "").strip()
    if latest_name:
        fallback, _ext = os.path.splitext(latest_name)
        return (fallback or latest_name)[:120]
    return ""

def _preferred_task_id_from_session(scene: str, context: dict[str, Any]) -> int | None:
    if scene in {"task_detail", "task_editor"}:
        return int(context.get("task_id") or context.get("target_id") or 0) or None
    if scene in {"search", "search_results", "tmdb_results"}:
        return int(context.get("replace_task_id") or 0) or None
    return None

def _short_link_text(value: str, limit: int = 56) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"

def _task_shareurl(shareurl: str, fid: str | None = None) -> str:
    raw = str(shareurl or "").strip()
    target_fid = str(fid or "").strip()
    if not raw:
        return raw
    if "yun.139.com" in raw or "caiyun.139.com" in raw:
        head, sep, frag = raw.partition("#")
        if sep:
            frag_path, frag_query = (frag.split("?", 1) + [""])[:2]
            pairs = [p for p in frag_query.split("&") if p and not p.startswith("fid=")]
            if target_fid and target_fid not in ("0", "root"):
                pairs.append(f"fid={target_fid}")
            next_frag = frag_path if not pairs else f"{frag_path}?{'&'.join(pairs)}"
            return f"{head}#{next_frag}".strip()
        head = re.sub(r"([?&])fid=[^&#]*", r"\1", head)
        head = re.sub(r"[?&]+$", "", head)
        head = re.sub(r"\?&", "?", head)
        if target_fid and target_fid not in ("0", "root"):
            head = f"{head}{'&' if '?' in head else '?'}fid={target_fid}"
        return head.strip()
    if not target_fid or target_fid == "0":
        match = re.search(r".*s/[a-zA-Z0-9\-_]+(\?[^#]*)?", raw)
        return str(match.group(0) if match else raw.split("#")[0]).strip()
    if target_fid in raw:
        match = re.search(rf".*/{re.escape(target_fid)}[^/]*", raw)
        if match:
            return str(match.group(0) or "").strip() or raw
    return f"{raw.split('#')[0]}#/list/share/{target_fid}"

def _extract_task_share_fid(shareurl: str) -> str | None:
    raw = str(shareurl or "").strip()
    if not raw:
        return None
    match_query = re.search(r"(?:\?|&)fid=([^&#]+)", raw)
    if match_query:
        fid = str(match_query.group(1) or "").strip()
        if fid and fid not in ("0", "root"):
            return fid
    match_hash = re.search(r"#/list/share/([a-zA-Z0-9]{6,64})", raw)
    if match_hash:
        return str(match_hash.group(1) or "").strip() or None
    match_tail = re.search(r"/([a-fA-F0-9]{32})-?[^/]*$", raw)
    if match_tail:
        return str(match_tail.group(1) or "").strip() or None
    return None

def _share_folder_label(shareurl: str, stack: list[dict[str, Any]] | None = None) -> str:
    parts = [str((item or {}).get("name") or "").strip() for item in (stack or []) if str((item or {}).get("name") or "").strip()]
    path = "/" + "/".join(parts) if parts else "/"
    fid = str(((stack or [])[-1] or {}).get("pdir_fid") or "").strip() if stack else str(_extract_task_share_fid(shareurl) or "").strip()
    return f"{path} · {fid}" if fid else f"根目录 ({path})"

def _tmdb_binding_label(draft: dict[str, Any]) -> str:
    tmdb_id_raw = _get_nested(draft, "tmdb_id")
    media_type = str(_get_nested(draft, "tmdb_media_type") or "").strip()
    try:
        tmdb_id = int(tmdb_id_raw or 0)
    except Exception:
        tmdb_id = 0
    if tmdb_id <= 0 or not media_type:
        return "未绑定"
    title = str(_get_nested(draft, "__tmdb_title__") or "").strip()
    if title:
        return f"{title} [{media_type} #{tmdb_id}]"
    return f"{media_type} #{tmdb_id}"

def _magic_rule_brief(item: dict[str, Any]) -> str:
    key = str(item.get("key") or "").strip()
    label = str(item.get("label") or "").strip()
    return f"{label}（{key}）" if label else key

def _magic_rule_binding_label(draft: dict[str, Any]) -> str:
    current = str(_get_nested(draft, "__magic_rule_label__") or "").strip()
    if current:
        return current
    pattern = str(_get_nested(draft, "pattern") or "").strip()
    replace = str(_get_nested(draft, "replace") or "").strip()
    if not pattern and not replace:
        return "未选择"
    if pattern.startswith("$"):
        return pattern
    return "手动填写"

def _display_datetime(value: Any) -> str:
    if value in (None, ""):
        return "-"
    raw = str(value).strip()
    if not raw:
        return "-"
    try:
        number = float(raw)
        if number > 0:
            if number > 1e12:
                number /= 1000.0
            dt = datetime.fromtimestamp(number)
            return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return raw

def _is_video_preview_item(item: dict[str, Any]) -> bool:
    name = str(item.get("file_name") or item.get("name") or "").strip().lower()
    return bool(re.search(r"\.(mp4|mkv|mov|m4v|avi|mpeg|ts|flv|wmv|webm|cas)$", name))
