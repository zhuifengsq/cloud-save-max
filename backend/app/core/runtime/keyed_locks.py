"""有上限的 key 级锁表（进程内）。

背景：素材去重、适配器实例创建等场景需要按 key 串行化，但直接用
`dict[key] = threading.Lock()` 会让锁表随 key 基数（目录 × 素材名 × 账号）
无限增长——这是长跑进程的隐性内存泄漏。

约束：
- 单进程假设：锁只在本进程内有效，多 worker 部署时跨进程互斥需外部协调
  （当前部署是单 uvicorn 进程，见 Dockerfile）；
- 淘汰只挑"当前没有被持有（locked() 为 False）"的锁，正在临界区里的锁
  一定不会被回收；极端情况下（所有锁都在用）会短暂超过 maxsize，等下次
  插入时再回收，不会死锁也不会丢锁；
- 不保证"get 返回同一把锁"的跨淘汰稳定性：被淘汰后同一 key 会拿到新锁。
  因此淘汰策略只能用于"同一 key 的并发窗口很短（毫秒~秒级）"的场景；
- 不在锁表内部做任何业务逻辑，锁的保护范围由调用方 with 决定。
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any, Hashable


class LRUKeyedLocks:
    """按 key 分配 threading.Lock，并按插入顺序（LRU）淘汰未使用项。"""

    def __init__(self, maxsize: int = 1024) -> None:
        if maxsize < 1:
            raise ValueError("maxsize 必须 >= 1")
        self._maxsize = int(maxsize)
        self._guard = threading.Lock()
        self._locks: "OrderedDict[Hashable, threading.Lock]" = OrderedDict()

    @property
    def maxsize(self) -> int:
        return self._maxsize

    def get(self, key: Hashable) -> threading.Lock:
        """返回 key 对应的锁；同一 key 在未被淘汰时始终返回同一把。"""
        with self._guard:
            lock = self._locks.get(key)
            if lock is not None:
                self._locks.move_to_end(key)
                return lock
            lock = threading.Lock()
            self._locks[key] = lock
            self._evict_locked(protect=key)
            return lock

    def _evict_locked(self, *, protect: Hashable | None = None) -> None:
        """持 _guard 时调用：从最旧到最新挑"未被持有"的锁淘汰。"""
        while len(self._locks) > self._maxsize:
            victim: Hashable | None = None
            for candidate, candidate_lock in self._locks.items():
                if candidate == protect:
                    continue
                if not candidate_lock.locked():
                    victim = candidate
                    break
            if victim is None:
                # 全部在用：宁可暂时超限，也不能回收正在临界区的锁。
                return
            self._locks.pop(victim, None)

    def clear(self) -> None:
        """清空锁表（只丢弃引用，已持有锁的线程照常执行）。"""
        with self._guard:
            self._locks.clear()

    def keys(self) -> list[Any]:
        with self._guard:
            return list(self._locks.keys())

    def __len__(self) -> int:
        with self._guard:
            return len(self._locks)

    def __contains__(self, key: Hashable) -> bool:
        with self._guard:
            return key in self._locks
