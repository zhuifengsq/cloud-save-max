"""通知端口：让通知在测试中可替换的稳定接口。

目标架构的稳定接口，不急于套用。现有 notifications.send / telegram 发送侧
迁移期可原地实现；通知失败只记录、不回滚业务事务（见 plan §3.6）。
"""
from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable


@runtime_checkable
class NotifierPort(Protocol):
    """发送一条通知。"""

    def send(
        self,
        title: str,
        message: str,
        *,
        level: str = "info",
        meta: Mapping[str, Any] | None = None,
    ) -> bool:
        """发送；返回是否至少有一个渠道成功。"""
