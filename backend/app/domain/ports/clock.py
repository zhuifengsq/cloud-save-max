"""时钟端口：把 datetime.now() 从业务代码里赶出去的稳定接口。

目标架构的稳定接口，不急于套用。测试用固定时钟即可复现「跨天/跨时区/超时」类 bug。
"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class ClockPort(Protocol):
    """时间来源（项目约定：业务时区 Asia/Shanghai，落库用本地时间）。"""

    def now(self) -> datetime:
        """当前本地时间（带 tzinfo 或不带由实现约定，调用方必须一致）。"""

    def now_utc(self) -> datetime:
        """当前 UTC 时间。"""

    def monotonic(self) -> float:
        """单调时钟（秒），用于耗时统计，不受系统时间调整影响。"""

    def sleep(self, seconds: float) -> None:
        """可替换的 sleep（测试中立即返回）。"""
