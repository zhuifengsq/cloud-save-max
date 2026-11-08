"""LRUKeyedLocks 回归测试（task-5 S1：key 级锁表有上限）。

覆盖：
1. 超过 maxsize 时按插入序淘汰最旧项；
2. 正在被持有的锁不会被淘汰（不能回收临界区里的锁）；
3. 同一 key 并发互斥，不同 key 不互相阻塞。
"""
from __future__ import annotations

import threading
import time

import pytest

from app.core.runtime.keyed_locks import LRUKeyedLocks


def test_evicts_oldest_when_over_capacity():
    locks = LRUKeyedLocks(maxsize=2)
    locks.get("a")
    locks.get("b")
    locks.get("c")

    assert len(locks) == 2
    assert "a" not in locks
    assert "c" in locks


def test_get_refreshes_recency_so_lru_order_matches_usage():
    locks = LRUKeyedLocks(maxsize=2)
    first = locks.get("a")
    locks.get("b")
    assert locks.get("a") is first  # a 变成最近使用
    locks.get("c")

    assert "a" in locks
    assert "b" not in locks


def test_locked_entry_is_not_evicted():
    locks = LRUKeyedLocks(maxsize=1)
    held = locks.get("held")
    assert held.acquire(blocking=False)
    try:
        for index in range(5):
            locks.get(f"other-{index}")
        assert "held" in locks
        assert locks.get("held") is held
        # 只有 held 被持有；其余都可在插入时回收，因此最多短暂超限 1 个。
        assert len(locks) <= 2
    finally:
        held.release()

    for index in range(5, 12):
        locks.get(f"other-{index}")
    assert len(locks) == 1


def test_same_key_is_mutually_exclusive():
    locks = LRUKeyedLocks(maxsize=8)
    active = 0
    peak = 0
    guard = threading.Lock()

    def worker() -> None:
        nonlocal active, peak
        with locks.get("shared"):
            with guard:
                active += 1
                peak = max(peak, active)
            time.sleep(0.01)
            with guard:
                active -= 1

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)

    assert peak == 1


def test_different_keys_do_not_block_each_other():
    locks = LRUKeyedLocks(maxsize=8)
    holding = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def holder() -> None:
        with locks.get("k1"):
            holding.set()
            release.wait(5)

    def other() -> None:
        assert holding.wait(5)
        with locks.get("k2"):
            finished.set()

    first = threading.Thread(target=holder)
    second = threading.Thread(target=other)
    first.start()
    second.start()
    try:
        assert finished.wait(5), "不同 key 的锁不应互相阻塞"
    finally:
        release.set()
        first.join(5)
        second.join(5)


def test_rejects_invalid_maxsize():
    with pytest.raises(ValueError):
        LRUKeyedLocks(maxsize=0)
