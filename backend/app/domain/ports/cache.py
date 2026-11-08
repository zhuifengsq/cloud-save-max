"""缓存端口：统一 TTL / 容量 / 淘汰语义的稳定接口。

目标架构的稳定接口，不急于套用。现有 12 处手写缓存（TTLCache、LRU、分片锁等）
迁移期可以原地实现本协议，调用方只依赖协议，不依赖具体缓存库。
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CachePort(Protocol):
    """最小键值缓存契约。"""

    def get(self, key: str) -> Any | None:
        """读取；未命中或已过期返回 None。"""

    def set(self, key: str, value: Any, ttl_seconds: float | None = None) -> None:
        """写入；ttl_seconds=None 表示不过期（由实现决定容量上限）。"""

    def delete(self, key: str) -> bool:
        """删除；返回是否命中。"""

    def clear(self) -> None:
        """清空（测试/配置热更新时使用）。"""
