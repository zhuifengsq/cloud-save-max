"""进程内 TTL 缓存（platform 层）：实现 app.domain.ports.cache.CachePort。

只依赖标准库；淘汰策略 = 到期即删 + LRU 上限。用途是「读多写少、短 TTL、可容忍
秒级滞后」的快照缓存（账号快照、配置快照），不用于业务状态。

单进程假设：多 worker / 多副本时每个进程各一份，不共享。跨进程缓存需要 Redis 等
新依赖，本期冻结（见 docs/architecture-evolution-plan.md 第 8 节）。
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Callable


class TtlCache:
    """带 TTL 与容量的进程内键值缓存。"""

    def __init__(
        self,
        *,
        maxsize: int = 256,
        ttl_seconds: float | None = 30.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if maxsize < 1:
            raise ValueError("maxsize 必须 >= 1")
        self._maxsize = int(maxsize)
        self._ttl = None if ttl_seconds is None else float(ttl_seconds)
        self._clock = clock or time.monotonic
        self._guard = threading.Lock()
        self._items: "OrderedDict[Any, tuple[float, Any]]" = OrderedDict()

    @property
    def maxsize(self) -> int:
        return self._maxsize

    @property
    def ttl_seconds(self) -> float | None:
        return self._ttl

    def get(self, key: Any) -> Any | None:
        """读取；未命中或已过期返回 None。"""
        with self._guard:
            entry = self._items.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at <= self._clock():
                self._items.pop(key, None)
                return None
            self._items.move_to_end(key)
            return value

    def set(self, key: Any, value: Any, ttl_seconds: float | None = None) -> None:
        """写入；ttl_seconds 缺省用构造时的 TTL，二者都为 None 表示不过期。"""
        effective = self._ttl if ttl_seconds is None else float(ttl_seconds)
        expires_at = float("inf") if effective is None or effective <= 0 else self._clock() + effective
        with self._guard:
            self._items.pop(key, None)
            self._items[key] = (expires_at, value)
            while len(self._items) > self._maxsize:
                self._items.popitem(last=False)

    def delete(self, key: Any) -> bool:
        """删除；返回是否命中。"""
        with self._guard:
            return self._items.pop(key, None) is not None

    def clear(self) -> None:
        """清空（测试/配置热更新时使用）。"""
        with self._guard:
            self._items.clear()

    def purge_expired(self) -> int:
        """清掉全部已过期项，返回清理条数。"""
        now = self._clock()
        with self._guard:
            expired = [key for key, (expires_at, _) in self._items.items() if expires_at <= now]
            for key in expired:
                self._items.pop(key, None)
            return len(expired)

    def __len__(self) -> int:
        with self._guard:
            return len(self._items)

    def __contains__(self, key: Any) -> bool:
        return self.get(key) is not None
