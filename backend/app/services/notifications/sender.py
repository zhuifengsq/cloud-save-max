from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.core.settings import settings
from app.services.notifications import legacy_notify
from app.services.notifications.settings import get_or_create_notification_setting, load_notification_config

logger = logging.getLogger(__name__)


def _get_channel_enabled_map(config: dict[str, Any]) -> dict[str, bool]:
    raw = (config or {}).get("__channel_enabled")
    if not isinstance(raw, dict):
        return {}
    result: dict[str, bool] = {}
    for key, value in raw.items():
        if isinstance(key, str):
            result[key] = bool(value)
    return result


def send_test(
    title: str,
    content: str,
    *,
    config: dict[str, Any],
    channels: list[str] | None = None,
) -> list[dict[str, Any]]:
    merged = dict(legacy_notify.DEFAULT_PUSH_CONFIG)
    merged.update(config or {})
    return legacy_notify.send(title, content, ignore_default_config=True, channels=channels, **merged)


def _should_send(config: dict[str, Any], title: str, content: str) -> bool:
    """检查通知是否应该发送（基于关键词过滤/排除）"""
    text = f"{title} {content}".lower()

    # 排除关键词
    exclude_keywords = [str(k).strip().lower() for k in (config.get("__exclude_keywords") or []) if str(k).strip()]
    if exclude_keywords:
        exclude_mode = str(config.get("__exclude_mode") or "any").strip()
        if exclude_mode == "all":
            if exclude_keywords and all(k in text for k in exclude_keywords):
                return False
        else:
            if any(k in text for k in exclude_keywords):
                return False

    # 包含关键词（只有包含才发送）
    filter_keywords = [str(k).strip().lower() for k in (config.get("__filter_keywords") or []) if str(k).strip()]
    if filter_keywords:
        filter_mode = str(config.get("__filter_mode") or "all").strip()
        if filter_mode == "any":
            if not any(k in text for k in filter_keywords):
                return False
        else:
            if not all(k in text for k in filter_keywords):
                return False

    return True


def send_runtime(
    db: Session,
    title: str,
    content: str,
    *,
    channels: list[str] | None = None,
) -> list[dict[str, Any]]:
    if not content:
        return []
    item = get_or_create_notification_setting(db)
    config = load_notification_config(item)
    merged = dict(legacy_notify.DEFAULT_PUSH_CONFIG)
    merged.update(config or {})
    if not _should_send(merged, title, content):
        return []
    if settings.use_notify_queue:
        # 通知在任务线程/调度线程里是尾部动作，但渠道 HTTP 最坏要等几十秒
        # （legacy_notify.send 会 join 每个渠道线程）。这里只做配置判断与入队，
        # 真正的扇出交给通知队列的唯一消费者线程，调用方立即返回。
        try:
            legacy_notify.send_async(
                title, content, ignore_default_config=True, channels=channels, **merged
            )
        except Exception:
            logger.warning("通知入队失败，已跳过本次通知: %s", title, exc_info=True)
        return []
    try:
        return legacy_notify.send(title, content, ignore_default_config=True, channels=channels, **merged)
    except Exception:
        return []
