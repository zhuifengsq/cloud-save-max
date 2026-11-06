from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.extensions.adapters.adapter_factory import AdapterFactory
from app.extensions.runtime.adapter_registry import AdapterRegistry
from app.models.task_execution import TaskExecution
from app.services.task_executions import history_limit
from app.services.task_share_links import get_task_share_link


logger = logging.getLogger(__name__)
SNAPSHOT_KEY = "generated_share_check"
FAILURE_STAGE = "generated_share_check"


def read_share_check(snapshot: str | None) -> dict:
    try:
        data = json.loads(snapshot or "{}")
    except (TypeError, ValueError):
        return {}
    check = data.get(SNAPSHOT_KEY) if isinstance(data, dict) else None
    return check if isinstance(check, dict) else {}


def _failure(drive_type: str, response: dict, fallback: str) -> tuple[str, str]:
    code = str(response.get("code", ""))
    message = str(response.get("message") or "").strip()
    if code == "41012" and drive_type == "quark":
        return "invalid", "分享已被取消"
    if drive_type == "baidu" and code in {"-1", "105", "145"}:
        return "invalid", {"-1": "分享链接不存在", "105": "分享链接已过期", "145": "分享链接已失效"}[code]
    if message in {"分享链接已失效", "分享链接已过期", "分享链接不存在", "分享已取消", "好友已取消了分享", "分享不存在"}:
        return "invalid", message
    if "提取码" in message or (drive_type == "baidu" and code in {"4", "200025"}):
        return "error", "分享提取码无法通过验证"
    if "限流" in message or "频率过高" in message or str(response.get("status")) == "429":
        return "error", "网盘请求频率过高，稍后重试"
    return "error", fallback


def _probe(record: dict, manager) -> tuple[str, str]:
    url = str(record["share_url"]).strip()
    drive_type = AdapterRegistry.detect_drive_type(url)
    if drive_type not in {"quark", "baidu"}:
        return "error", "该网盘暂不支持检测生成的分享链接"
    if record.get("drive_type") and record["drive_type"] != drive_type:
        return "error", "分享链接与保存的网盘类型不一致"
    adapter = manager.get_adapter_for_task(
        {"shareurl": url, "account_name": record.get("account_name")}, allow_inactive=True,
    )
    if adapter is None or adapter.DRIVE_TYPE != drive_type:
        return "error", "没有可用于检测的网盘账号"
    if not adapter.ensure_initialized(ttl_seconds=AdapterFactory.init_ttl_seconds()):
        return "error", "网盘账号登录失败"

    pwd_id, url_passcode, folder_id, _ = adapter.extract_url(url)
    if not pwd_id:
        return "error", "无法解析生成的分享链接"
    token_options = {"check_errno": True} if drive_type == "baidu" else {}
    token = adapter.get_stoken(pwd_id, record.get("passcode") or url_passcode or "", **token_options) or {}
    stoken = (token.get("data") or {}).get("stoken")
    if not stoken:
        return _failure(drive_type, token, "网盘暂时无法验证分享链接")

    options = {"first_page": True} if drive_type == "quark" else {}
    detail = adapter.get_detail(pwd_id, stoken, str(folder_id or "0"), **options) or {}
    if str(detail.get("code")) == "0" and isinstance((detail.get("data") or {}).get("list"), list):
        return "valid", "分享目录可以正常打开"
    return _failure(drive_type, detail, "网盘暂时无法读取分享目录")


def check_generated_share(db: Session, task, manager) -> dict | None:
    record = get_task_share_link(task)
    if record is None:
        return None

    identity = {key: str(record.get(key) or "") for key in ("share_id", "saved_at")}
    last_definitive = None
    try:
        with db.no_autoflush:
            snapshots = db.scalars(
                select(TaskExecution.adapter_snapshot)
                .where(TaskExecution.task_id == task.id)
                .order_by(TaskExecution.id.desc())
                .limit(history_limit())
            ).all()
        for snapshot in snapshots:
            previous = read_share_check(snapshot)
            if all(previous.get(key) == value for key, value in identity.items()):
                last_definitive = previous.get("last_definitive")
                if last_definitive in {"valid", "invalid"}:
                    break
        status, message = _probe(record, manager)
    except (OSError, TimeoutError):
        status, message = "error", "连接网盘失败或超时，稍后重试"
    except Exception:
        logger.warning("生成的分享链接检测未完成 task_id=%s", task.id)
        status, message = "error", "网盘暂时无法完成分享链接检测"
    return {
        **identity,
        "status": status,
        "message": message,
        "checked_at": datetime.now().astimezone().isoformat(),
        "last_definitive": status if status in {"valid", "invalid"} else last_definitive,
        "notify": status == "invalid" and last_definitive != "invalid",
    }
