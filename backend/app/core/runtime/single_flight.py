"""进程内 single-flight（同 key 并发只执行一次）。

用于「首次创建代价高、并发重复创建有害」的场景：适配器实例创建（可能触发网盘
登录 IO）、缓存首次回填等。语义与 golang.org/x/sync/singleflight 一致：

- 第一个到达的调用者是 leader，执行 factory；
- 同 key 的其他调用者等待 leader 结束，然后拿到同一结果（或同一异常对象）；
- leader 结束后立刻从 _flights 移除，后来的调用者开启新 flight（不缓存结果，
  缓存由调用方的实例缓存/缓存层负责）；
- 不重试、不做超时：超时策略由调用方（锁/HTTP 客户端）决定。

单进程假设：只在本进程内合并并发；多 worker 部署时每个进程各自 flight。
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Hashable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class _Flight:
    __slots__ = ("done", "value", "error")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.value: Any = None
        self.error: BaseException | None = None


class SingleFlight:
    """按 key 合并并发调用（满足 app.domain.ports.locks.SingleFlightPort）。"""

    def __init__(self, maxsize: int = 256) -> None:
        if maxsize < 1:
            raise ValueError("maxsize 必须 >= 1")
        self._maxsize = int(maxsize)
        self._guard = threading.Lock()
        self._flights: dict[Hashable, _Flight] = {}

    @property
    def maxsize(self) -> int:
        return self._maxsize

    def run(self, key: Hashable, factory: Callable[[], T]) -> T:
        with self._guard:
            flight = self._flights.get(key)
            leader = flight is None
            if leader:
                flight = _Flight()
                self._flights[key] = flight
                if len(self._flights) > self._maxsize:
                    # 软上限：只在并发 key 数异常时告警，不丢弃在途 flight（否则会丢等待者）。
                    logger.warning(
                        "SingleFlight flight 数超上限: %s > %s", len(self._flights), self._maxsize
                    )

        assert flight is not None
        if not leader:
            flight.done.wait()
            if flight.error is not None:
                raise flight.error
            return flight.value  # type: ignore[no-any-return]

        try:
            flight.value = factory()
            return flight.value  # type: ignore[no-any-return]
        except BaseException as exc:  # noqa: BLE001 - 原样转交给等待者
            flight.error = exc
            raise
        finally:
            with self._guard:
                self._flights.pop(key, None)
            flight.done.set()

    def __len__(self) -> int:
        with self._guard:
            return len(self._flights)

    def __contains__(self, key: Hashable) -> bool:
        with self._guard:
            return key in self._flights
