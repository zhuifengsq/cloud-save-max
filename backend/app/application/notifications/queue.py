"""通知发送队列（单消费者线程）。

为什么需要它：`legacy_notify.send()` 在调用线程里为每个通知渠道起一个线程并
`join()` 等它们结束（legacy_notify.py:793-795），渠道 HTTP 超时可达数十秒。
任务执行线程、调度线程、请求线程只要直接调用 send()，就会被通知网络 IO 拖住。

设计（不引入新依赖）：
- `queue.Queue(maxsize=1000)` + 唯一 daemon 消费者线程，首次 submit 时惰性启动；
- 生产者只入队，队列满时记 WARNING 并丢弃（返回 False），绝不阻塞业务线程；
- 消费者异常不会退出循环（单条失败记日志后继续），否则后续通知永久丢失；
- 本模块不依赖 app 内其它业务模块（避免 notifications 包出现导入环），
  真正的发送函数由调用方以 handler 注入（见 legacy_notify.get_notify_queue）；
- 单进程假设：队列在进程内，进程退出时未消费的通知会丢（通知本身是尽力而为语义）。

开关：`settings.use_notify_queue`。为 False 时 legacy_notify.send_async() 退化为
同步 send()，调用方无需感知。
"""

from __future__ import annotations

import logging
import queue as queue_module
import threading
from typing import Any, Callable

logger = logging.getLogger(__name__)

DEFAULT_MAXSIZE = 1000

# handler(title, content, channels, kwargs)
NotifyHandler = Callable[[str, str, "list[str] | None", "dict[str, Any]"], None]


class NotifyQueue:
    """单消费者通知队列；可直接实例化用于测试（注入 handler / autostart）。"""

    def __init__(
        self,
        maxsize: int = DEFAULT_MAXSIZE,
        *,
        handler: NotifyHandler,
        autostart: bool = True,
    ) -> None:
        if maxsize < 1:
            raise ValueError("maxsize 必须 >= 1")
        self._maxsize = int(maxsize)
        self._queue: "queue_module.Queue[tuple[str, str, list[str] | None, dict[str, Any]]]" = queue_module.Queue(
            maxsize=self._maxsize
        )
        self._handler: NotifyHandler = handler
        self._autostart = bool(autostart)
        self._start_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    @property
    def maxsize(self) -> int:
        return self._maxsize

    @property
    def worker(self) -> threading.Thread | None:
        return self._thread

    def qsize(self) -> int:
        return self._queue.qsize()

    def start(self) -> None:
        """启动消费者线程（幂等：已在运行则直接返回）。"""
        with self._start_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            thread = threading.Thread(target=self._consume, name="notify-queue", daemon=True)
            self._thread = thread
            thread.start()

    def _consume(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is None:  # 预留的停止哨兵
                    return
                title, content, channels, kwargs = item
                try:
                    self._handler(title, content, channels, kwargs)
                except Exception:
                    # 单条通知失败不能杀死消费者，否则队列里后面的通知再也不会被处理。
                    logger.exception("通知队列消费失败，跳过该条通知: %s", title)
            finally:
                self._queue.task_done()

    def submit(
        self,
        title: str,
        content: str,
        *,
        channels: "list[str] | None" = None,
        kwargs: "dict[str, Any] | None" = None,
    ) -> bool:
        """入队；成功 True，队列满丢弃并返回 False（不阻塞、不抛异常）。"""
        if self._autostart:
            self.start()
        try:
            self._queue.put_nowait((title, content, channels, dict(kwargs or {})))
            return True
        except queue_module.Full:
            logger.warning("通知队列已满（maxsize=%s），丢弃通知: %s", self._maxsize, title)
            return False

    def join(self, timeout: float | None = None) -> bool:
        """等待队列中已有通知被消费完（测试/优雅退出用）。"""
        with self._queue.all_tasks_done:
            return bool(
                self._queue.all_tasks_done.wait_for(lambda: self._queue.unfinished_tasks == 0, timeout)
            )
