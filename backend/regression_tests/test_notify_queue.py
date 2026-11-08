"""通知队列回归测试（task-5 S1：通知走单消费者队列）。

覆盖：
1. 队列满时丢弃并返回 False，绝不阻塞生产者；
2. 消费者处理某条通知抛异常时不退出，后续通知继续被消费；
3. use_notify_queue=False 时 send_async 退化为同步 send。
"""
from __future__ import annotations

import threading

from app.core.settings import settings
from app.services.notifications import legacy_notify
from app.services.notifications.queue import NotifyQueue


def test_full_queue_drops_instead_of_blocking():
    processed: list[str] = []
    queue = NotifyQueue(maxsize=1, handler=lambda title, c, ch, kw: processed.append(title), autostart=False)

    assert queue.submit("first", "content") is True
    assert queue.submit("second", "content") is False  # 满：丢弃，不阻塞

    queue.start()
    assert queue.join(5)
    assert processed == ["first"]


def test_consumer_survives_handler_exception():
    calls: list[str] = []
    second_done = threading.Event()

    def handler(title: str, content: str, channels, kwargs) -> None:
        calls.append(title)
        if title == "boom":
            raise RuntimeError("channel down")
        second_done.set()

    queue = NotifyQueue(maxsize=10, handler=handler)
    assert queue.submit("boom", "x") is True
    assert queue.submit("ok", "y") is True

    assert second_done.wait(5), "消费者在单条失败后应继续处理后续通知"
    assert queue.join(5)
    assert calls[:2] == ["boom", "ok"]
    assert queue.worker is not None and queue.worker.is_alive()


def test_send_async_falls_back_to_sync_when_flag_off(monkeypatch):
    calls: list[tuple[tuple, dict]] = []

    def fake_send(*args, **kwargs):
        calls.append((args, kwargs))
        return []

    monkeypatch.setattr(settings, "use_notify_queue", False)
    monkeypatch.setattr(legacy_notify, "send", fake_send)

    assert legacy_notify.send_async("title", "content") is True
    assert calls and calls[0][0] == ("title", "content")
