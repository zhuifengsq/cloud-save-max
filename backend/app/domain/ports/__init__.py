"""端口协议导出（纯 typing.Protocol，零实现、零 IO）。

目标架构的稳定接口，不急于套用；实现类不需要继承，结构匹配即可。
"""
from app.domain.ports.cache import CachePort
from app.domain.ports.clock import ClockPort
from app.domain.ports.drive import DriveGatewayPort, RemoteEntry, ShareProbeResult
from app.domain.ports.locks import KeyedLockPort, SingleFlightPort
from app.domain.ports.notify import NotifierPort
from app.domain.ports.task import TaskRunUseCasePort

__all__ = [
    "CachePort",
    "ClockPort",
    "DriveGatewayPort",
    "KeyedLockPort",
    "NotifierPort",
    "RemoteEntry",
    "ShareProbeResult",
    "SingleFlightPort",
    "TaskRunUseCasePort",
]
