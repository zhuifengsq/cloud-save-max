"""按 key 的进程内限速器（platform 层）。

替换「每个适配器实例一把锁 + 持锁随机 sleep」的旧实现（base_adapter._throttle_request）：
旧实现只能约束同一个实例，而实例缓存会因 TTL / cookie 轮换出现同账户多实例并存，
于是同账户的并发请求仍然可能同时打到网盘，触发风控或 403。

本实现按「网盘类型 + 账户」维度排队：用「预约下一个可用时间点」的方式保证相邻两次
请求的发起时刻至少间隔一个随机区间，且排队不持有实例锁（不同账户互不阻塞）。

单进程假设：多 worker / 多副本时每个进程各自限速，不能替代网盘侧配额。
"""
from __future__ import annotations

import random
import threading
import time
from collections import OrderedDict
from typing import Callable


class KeyedRateLimiter:
    """同 key 的调用按间隔排队；不同 key 互不影响。"""

    def __init__(
        self,
        *,
        maxsize: int = 1024,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        if maxsize < 1:
            raise ValueError("maxsize 必须 >= 1")
        self._maxsize = int(maxsize)
        self._clock = clock or time.monotonic
        self._sleeper = sleeper or time.sleep
        self._guard = threading.Lock()
        self._next_at: "OrderedDict[str, float]" = OrderedDict()

    @property
    def maxsize(self) -> int:
        return self._maxsize

    def wait(self, key: str, min_interval: float, max_interval: float | None = None) -> float:
        """阻塞到该 key 的下一个可用时间点，返回实际等待秒数。"""
        min_interval = max(0.0, float(min_interval))
        if max_interval is None or float(max_interval) <= min_interval:
            interval = min_interval
        else:
            interval = random.uniform(min_interval, float(max_interval))

        now = self._clock()
        with self._guard:
            slot = self._next_at.get(key, 0.0)
            if slot < now:
                slot = now
            self._next_at[key] = slot + interval
            self._next_at.move_to_end(key)
            self._evict_locked(now)
        delay = slot - now
        if delay > 0:
            self._sleeper(delay)
        return max(0.0, delay)

    def _evict_locked(self, now: float) -> None:
        if len(self._next_at) <= self._maxsize:
            return
        stale = [key for key, next_at in self._next_at.items() if next_at <= now]
        for key in stale:
            if len(self._next_at) <= self._maxsize:
                return
            self._next_at.pop(key, None)
        while len(self._next_at) > self._maxsize:
            self._next_at.popitem(last=False)

    def next_allowed_at(self, key: str) -> float:
        """该 key 下一次可发请求的时刻（测试/观测用）。"""
        with self._guard:
            return self._next_at.get(key, 0.0)

    def clear(self) -> None:
        with self._guard:
            self._next_at.clear()

    def __len__(self) -> int:
        with self._guard:
            return len(self._next_at)


_LIMITER: KeyedRateLimiter | None = None
_LIMITER_LOCK = threading.Lock()


def get_rate_limiter() -> KeyedRateLimiter:
    """进程级单例（适配器实例共享）。"""
    global _LIMITER
    if _LIMITER is None:
        with _LIMITER_LOCK:
            if _LIMITER is None:
                _LIMITER = KeyedRateLimiter()
    return _LIMITER


def reset_rate_limiter(limiter: KeyedRateLimiter | None = None) -> None:
    """替换/清空单例（测试用）。"""
    global _LIMITER
    with _LIMITER_LOCK:
        _LIMITER = limiter
