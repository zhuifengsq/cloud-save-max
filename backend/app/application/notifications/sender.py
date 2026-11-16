from __future__ import annotations

import logging
import threading
from typing import Any

from sqlalchemy.orm import Session

from app.core.settings import settings
from app.application.notifications import channel_delivery
from app.application.notifications.channel_delivery import DEFAULT_PUSH_CONFIG
from app.application.notifications.filtering import _should_send
from app.application.notifications.queue import DEFAULT_MAXSIZE, NotifyQueue
from app.application.notifications.settings import get_or_create_notification_setting, load_notification_config

logger = logging.getLogger(__name__)


def send_test(
    title: str,
    content: str,
    *,
    config: dict[str, Any],
    channels: list[str] | None = None,
) -> list[dict[str, Any]]:
    merged = dict(DEFAULT_PUSH_CONFIG)
    merged.update(config or {})
    return send(title, content, ignore_default_config=True, channels=channels, **merged)


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
    merged = dict(DEFAULT_PUSH_CONFIG)
    merged.update(config or {})
    if not _should_send(merged, title, content):
        return []
    if settings.use_notify_queue:
        # 通知在任务线程/调度线程里是尾部动作，但渠道 HTTP 最坏要等几十秒
        # （send 会 join 每个渠道线程）。这里只做配置判断与入队，
        # 真正的扇出交给通知队列的唯一消费者线程，调用方立即返回。
        try:
            send_async(
                title, content, ignore_default_config=True, channels=channels, **merged
            )
        except Exception as exc:
            logger.warning("Notification enqueue failed error_type=%s", type(exc).__name__)
        return []
    try:
        return send(title, content, ignore_default_config=True, channels=channels, **merged)
    except Exception as exc:
        logger.warning("Notification delivery failed error_type=%s", type(exc).__name__)
        return []


def send(title: str, content: str, ignore_default_config: bool = False, channels: list[str] | None = None, **kwargs):
    """Synchronous channel dispatch; used by test notifications and queue consumer."""
    return channel_delivery.send(title, content, ignore_default_config=ignore_default_config, channels=channels, **kwargs)


_notify_queue: "object | None" = None
_notify_queue_lock = threading.Lock()


def _deliver_notification(title: str, content: str, channels: list[str] | None, kwargs: dict) -> None:
    """队列消费者入口：复用现有 send()（渠道扇出与返回结构不变）。"""
    send(title, content, channels=channels, **kwargs)


def get_notify_queue():
    """进程内通知队列单例；首次调用时创建，消费者线程在首次 submit 时启动。"""
    global _notify_queue

    with _notify_queue_lock:
        if _notify_queue is None:
            _notify_queue = NotifyQueue(maxsize=DEFAULT_MAXSIZE, handler=_deliver_notification)
        return _notify_queue


def reset_notify_queue() -> None:
    """测试用：丢弃队列单例，下一次 send_async 重新创建。"""
    global _notify_queue
    with _notify_queue_lock:
        _notify_queue = None


def send_async(
    title: str,
    content: str,
    *,
    channels: list[str] | None = None,
    **kwargs,
) -> bool:
    """异步发送通知：入队后立即返回，渠道扇出由通知队列的唯一消费者线程执行。

    - use_notify_queue=True（默认）：入队，队列满时丢弃并返回 False；
    - use_notify_queue=False：回退为同步 send()，保持旧的阻塞语义（返回 True）。
    send() 本身的同步语义完全不变，这里只是多一条非阻塞入口。
    """

    if not settings.use_notify_queue:
        send(title, content, channels=channels, **kwargs)
        return True

    return get_notify_queue().submit(title, content, channels=channels, kwargs=kwargs)
