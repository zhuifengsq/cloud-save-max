"""S4-5 横切组件护栏：TTL 缓存 / 按 key 限速 / 令牌刷新 single-flight。

这些组件都是「平台层」纯内存实现，不碰数据库、不发网络请求，因此可以直接单测。
"""
from __future__ import annotations

import threading
import time

import pytest

from app.core.runtime.rate_limiter import KeyedRateLimiter
from app.core.runtime.ttl_cache import TtlCache
from app.core.runtime.token_manager import TokenManager, TokenRefreshOutcome


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ---------- TtlCache ----------


def test_ttl_cache_hit_miss_and_delete():
    cache = TtlCache(maxsize=4, ttl_seconds=30)
    assert cache.get("a") is None
    cache.set("a", {"v": 1})
    assert cache.get("a") == {"v": 1}
    assert len(cache) == 1
    assert "a" in cache
    assert cache.delete("a") is True
    assert cache.get("a") is None
    assert cache.delete("a") is False


def test_ttl_cache_expires_after_ttl():
    clock = FakeClock()
    cache = TtlCache(ttl_seconds=30, clock=clock)
    cache.set("a", 1)
    clock.advance(29)
    assert cache.get("a") == 1
    clock.advance(2)
    assert cache.get("a") is None
    assert len(cache) == 0


def test_ttl_cache_lru_eviction_and_purge():
    clock = FakeClock()
    cache = TtlCache(maxsize=2, ttl_seconds=10, clock=clock)
    cache.set("a", 1)
    cache.set("b", 2)
    assert cache.get("a") == 1  # a 变成最近使用
    cache.set("c", 3)  # 淘汰 b
    assert cache.get("b") is None
    assert cache.get("a") == 1
    assert cache.get("c") == 3
    clock.advance(11)
    assert cache.purge_expired() == 2
    assert len(cache) == 0


def test_ttl_cache_without_ttl_never_expires():
    clock = FakeClock()
    cache = TtlCache(ttl_seconds=None, clock=clock)
    cache.set("a", 1)
    clock.advance(10 ** 6)
    assert cache.get("a") == 1


# ---------- KeyedRateLimiter ----------


def _limiter_with_clock(maxsize: int = 8):
    clock = FakeClock()
    sleeps: list[float] = []

    def sleeper(seconds: float) -> None:
        sleeps.append(seconds)
        clock.advance(seconds)

    limiter = KeyedRateLimiter(maxsize=maxsize, clock=clock, sleeper=sleeper)
    return limiter, sleeps


def test_rate_limiter_spaces_same_key():
    limiter, sleeps = _limiter_with_clock()
    assert limiter.wait("k", 1.0, 1.0) == 0.0
    assert sleeps == []
    assert limiter.wait("k", 1.0, 1.0) == pytest.approx(1.0)
    assert sleeps == [pytest.approx(1.0)]
    assert limiter.wait("k", 1.0, 1.0) == pytest.approx(1.0)
    assert len(sleeps) == 2


def test_rate_limiter_different_keys_do_not_block():
    limiter, sleeps = _limiter_with_clock()
    limiter.wait("a", 2.0, 2.0)
    limiter.wait("b", 2.0, 2.0)
    assert sleeps == []
    assert len(limiter) == 2
    assert limiter.next_allowed_at("a") > limiter.next_allowed_at("b") - 0.001


def test_rate_limiter_bounded():
    limiter, sleeps = _limiter_with_clock(maxsize=2)
    for key in ("a", "b", "c"):
        limiter.wait(key, 5.0, 5.0)
    assert len(limiter) == 2


# ---------- TokenManager ----------


def test_token_manager_short_circuits_when_valid():
    calls: list[int] = []
    manager = TokenManager()
    outcome = manager.refresh("acct", is_valid=lambda: True, refresh=lambda: calls.append(1) or True)
    assert outcome == TokenRefreshOutcome(performed=False, ok=True)
    assert calls == []


def test_token_manager_single_flight_merges_concurrent_refreshes():
    manager = TokenManager()
    calls: list[int] = []
    armed = threading.Event()
    release = threading.Event()
    state = {"valid": False}

    def refresh() -> bool:
        calls.append(1)
        armed.set()
        release.wait(5)
        state["valid"] = True
        return True

    outcomes: list[TokenRefreshOutcome] = []

    def worker() -> None:
        outcomes.append(manager.refresh("acct", is_valid=lambda: state["valid"], refresh=refresh))

    leader = threading.Thread(target=worker)
    leader.start()
    assert armed.wait(5)
    followers = [threading.Thread(target=worker) for _ in range(3)]
    for thread in followers:
        thread.start()
    time.sleep(0.05)
    release.set()
    leader.join(5)
    for thread in followers:
        thread.join(5)

    assert len(calls) == 1, "并发刷新必须被合并为一次"
    assert len(outcomes) == 4
    assert all(item.ok and item.performed for item in outcomes)


def test_token_manager_reports_failed_refresh():
    manager = TokenManager()
    outcome = manager.refresh("acct", is_valid=lambda: False, refresh=lambda: False)
    assert outcome.performed is True
    assert outcome.ok is False


def test_token_manager_propagates_exception():
    manager = TokenManager()

    def boom() -> None:
        raise RuntimeError("refresh failed")

    with pytest.raises(RuntimeError, match="refresh failed"):
        manager.refresh("acct", is_valid=lambda: False, refresh=boom)
