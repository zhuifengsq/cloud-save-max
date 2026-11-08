"""端口接入护栏：domain/ports 的协议必须有真实实现。

背景（docs/architecture-evolution-plan.md §10.11）：端口层建成后 8 个 Protocol 一度
0 引用（只有 __init__ 自身），等于纸面架构。本文件用 runtime_checkable 的
isinstance 做结构性验收：实现类必须真的具备端口方法，否则 isinstance 直接 False。

注意：runtime_checkable 只检查方法名存在，不检查签名——签名一致性由调用点
（mypy 不可用时靠 code review + 集成测试）保证。
"""
from __future__ import annotations

import threading
import time
from datetime import datetime

import pytest

from app.core.runtime.clock import SystemClock
from app.core.runtime.keyed_locks import LRUKeyedLocks
from app.core.runtime.single_flight import SingleFlight
from app.domain.ports import (
    ClockPort,
    KeyedLockPort,
    SingleFlightPort,
    TaskRunUseCasePort,
)


def test_keyed_locks_implement_port():
    locks = LRUKeyedLocks(maxsize=4)
    assert isinstance(locks, KeyedLockPort)

    assert locks.acquire("k") is True
    assert locks.get("k").locked() is True
    locks.release("k")
    assert locks.get("k").locked() is False

    # keyed() 是 with 用法糖：异常也要释放
    with pytest.raises(RuntimeError):
        with locks.keyed("k2"):
            assert locks.get("k2").locked() is True
            raise RuntimeError("boom")
    assert locks.get("k2").locked() is False

    # 已被持有时 acquire(timeout) 返回 False（不抛异常）
    assert locks.acquire("k3") is True
    try:
        assert locks.acquire("k3", timeout_seconds=0.05) is False
    finally:
        locks.release("k3")

    # 未持有的 key release 不应抛错
    locks.release("never-acquired")


def test_single_flight_implements_port():
    flight = SingleFlight(maxsize=8)
    assert isinstance(flight, SingleFlightPort)

    calls: list[int] = []
    started = threading.Event()
    release = threading.Event()

    def factory() -> str:
        calls.append(1)
        started.set()
        release.wait(2.0)
        return "value"

    results: list[str] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            results.append(flight.run("same-key", factory))
        except BaseException as exc:  # noqa: BLE001 - 测试收集
            errors.append(exc)

    first = threading.Thread(target=worker)
    first.start()
    assert started.wait(2.0)
    second = threading.Thread(target=worker)
    second.start()
    time.sleep(0.1)
    release.set()
    first.join(2.0)
    second.join(2.0)

    assert errors == []
    assert calls == [1], "同 key 并发只能执行一次 factory"
    assert results == ["value", "value"]
    assert len(flight) == 0, "flight 结束后必须移出注册表"

    # 结果不缓存：下一次调用重新执行 factory
    assert flight.run("same-key", lambda: "again") == "again"
    assert len(flight) == 0


def test_single_flight_propagates_error_to_waiters():
    flight = SingleFlight(maxsize=4)
    release = threading.Event()

    def factory() -> str:
        release.wait(2.0)
        raise ValueError("factory failed")

    errors: list[BaseException] = []

    def worker() -> None:
        try:
            flight.run("k", factory)
        except BaseException as exc:  # noqa: BLE001 - 测试收集
            errors.append(exc)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    time.sleep(0.05)
    t2.start()
    time.sleep(0.05)
    release.set()
    t1.join(2.0)
    t2.join(2.0)

    assert len(errors) == 2, "leader 与等待者都必须看到同一个异常"
    assert all(isinstance(e, ValueError) and str(e) == "factory failed" for e in errors)
    assert len(flight) == 0


def test_task_run_use_case_implements_port():
    from app.application.use_cases.run_task import RunTaskUseCase

    # db 只是透传给 TaskExecutor，构造阶段不访问数据库
    use_case = RunTaskUseCase(None)
    assert isinstance(use_case, TaskRunUseCasePort)


def test_system_clock_implements_port():
    clock = SystemClock()
    assert isinstance(clock, ClockPort)
    assert isinstance(clock.now(), datetime)
    assert isinstance(clock.now_utc(), datetime)
    assert clock.now_utc().tzinfo is None, "仓库约定 naive UTC"
    assert clock.monotonic() <= clock.monotonic()
    clock.sleep(0.01)


def test_clock_port_is_wired_in_production():
    """时间源真的走了 container.clock()（而不是每个服务各自 datetime.now）。"""
    from app.application import container
    from app.application import share_preview_batch_cache as spbc

    assert spbc.clock is container.clock
    assert isinstance(container.clock(), ClockPort)
    delta = abs((spbc._utcnow() - datetime.now()).total_seconds())
    assert delta < 5
