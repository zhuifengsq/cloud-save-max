"""锁端口：keyed lock 与 single-flight 的稳定接口。

目标架构的稳定接口，不急于套用。用于替换当前只增不删的三处注册表
（_LEAD_IN_LOCKS、adapter_factory._instance_cache、drive_account_auth_sessions._SESSIONS），
实现必须有上限与淘汰策略（见 docs/architecture-evolution-plan.md §3.7）。
"""
from __future__ import annotations

from typing import Callable, ContextManager, Protocol, TypeVar, runtime_checkable

T = TypeVar("T")


@runtime_checkable
class KeyedLockPort(Protocol):
    """按 key 加锁；同一 key 的并发调用串行。"""

    def acquire(self, key: str, timeout_seconds: float | None = None) -> bool:
        """尝试获取；成功返回 True，超时返回 False（不抛异常）。"""

    def release(self, key: str) -> None:
        """释放；由持有者调用，未持有时实现应忽略或报错（需在实现中约定）。"""

    def keyed(self, key: str, timeout_seconds: float | None = None) -> ContextManager[None]:
        """with 用法糖：进入即 acquire，退出即 release（异常也释放）。"""


@runtime_checkable
class SingleFlightPort(Protocol):
    """同 key 并发只执行一次，其余调用等待并复用结果。"""

    def run(self, key: str, factory: Callable[[], T]) -> T:
        """执行 factory；异常时释放等待者并原样抛出。"""
