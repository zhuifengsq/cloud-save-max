from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.application.container import drive_gateway, task_executions_repo
from app.application.container import adapter_factory as _acc_AdapterFactory; AdapterFactory = _acc_AdapterFactory()
from app.application.container import adapter_registry as _acc_AdapterRegistry; AdapterRegistry = _acc_AdapterRegistry()
from app.application.task_executions import history_limit
from app.application.task_share_links import get_task_share_link


logger = logging.getLogger(__name__)
SNAPSHOT_KEY = "generated_share_check"
FAILURE_STAGE = "generated_share_check"


def read_share_check(snapshot: str | None) -> dict:
    if isinstance(snapshot, dict):
        check = snapshot.get(SNAPSHOT_KEY)
        return dict(check) if isinstance(check, dict) else {}
    try:
        data = snapshot if isinstance(snapshot, dict) else json.loads(snapshot or "{}")
    except (TypeError, ValueError):
        return {}
    check = data.get(SNAPSHOT_KEY) if isinstance(data, dict) else None
    return check if isinstance(check, dict) else {}


def _map_kind(kind: str, message: str, fallback: str) -> tuple[str, str]:
    """把失败分类映射成本功能的状态与提示文案。

    判定逻辑由各网盘适配器的 classify_share_failure 负责（端口透传 kind/message），这里只管：
    - 确定为失效（invalid）才提示用户，其余一律按「检测失败」处理，避免误报。
    """
    kind = str(kind or "unknown")
    message = str(message or "").strip()
    if kind == "invalid":
        return "invalid", message or fallback
    if kind == "pending":
        # 审核中是暂时状态（网盘审核通过后分享自动恢复），不能算「检测失败」更不能通知。
        return "pending", message or "分享正在审核中，网盘审核通过后会自动恢复有效"
    if kind == "passcode":
        return "error", "分享提取码无法通过验证"
    if kind == "throttled":
        return "error", "网盘请求频率过高，稍后重试"
    if kind == "auth":
        return "error", message or "网盘账号登录失败"
    return "error", fallback


def _failure(adapter, response: dict, fallback: str) -> tuple[str, str]:
    """兼容入口：直接拿适配器响应做分类（护栏测试在用）。"""
    verdict = adapter.classify_share_failure(response) or {}
    return _map_kind(verdict.get("kind"), verdict.get("message"), fallback)


def _probe(record: dict, manager) -> tuple[str, str]:
    """探测生成的分享链接（走 DriveGatewayPort，业务文案留在本层）。"""
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

    gateway = drive_gateway(adapter, drive_type=drive_type)
    result = gateway.probe_share(url, passcode=str(record.get("passcode") or ""))
    if result.valid:
        return "valid", "分享目录可以正常打开"
    if result.stage == "parse":
        return "error", "无法解析生成的分享链接"
    fallback = "网盘暂时无法验证分享链接" if result.stage == "verify" else "网盘暂时无法读取分享目录"
    return _map_kind(result.kind, result.message, fallback)


def prepare_generated_share_check(db: Session, task) -> dict | None:
    record = get_task_share_link(task)
    if record is None:
        return None
    identity = {key: str(record.get(key) or "") for key in ("share_id", "saved_at")}
    last_definitive = None
    snapshots = task_executions_repo().list_recent_adapter_snapshots(db, task_id=task.id, limit=history_limit())
    for snapshot in snapshots:
        previous = read_share_check(snapshot)
        if all(previous.get(key) == value for key, value in identity.items()):
            last_definitive = previous.get("last_definitive")
            if last_definitive in {"valid", "invalid"}:
                break
    return {"record": dict(record), "identity": identity, "last_definitive": last_definitive,
            "task_id": task.id}


def probe_generated_share_check(plan, manager, *, always_notify=False):
    if plan is None:
        return None
    last_definitive = plan["last_definitive"]
    try:
        status, message = _probe(plan["record"], manager)
    except (OSError, TimeoutError):
        status, message = "error", "连接网盘失败或超时，稍后重试"
    except Exception:
        logger.warning("生成的分享链接检测未完成 task_id=%s", plan['task_id'])
        status, message = "error", "网盘暂时无法完成分享链接检测"
    return {
        **plan["identity"], "status": status, "message": message,
        "checked_at": datetime.now().astimezone().isoformat(),
        "last_definitive": status if status in {"valid", "invalid"} else last_definitive,
        "notify": status == "invalid" and (always_notify or last_definitive != "invalid"),
    }


def check_generated_share(db: Session, task, manager, *, always_notify: bool = False):
    from app.core.runtime.tx import end_read_tx
    plan = prepare_generated_share_check(db, task)
    end_read_tx(db)
    return probe_generated_share_check(plan, manager, always_notify=always_notify)
