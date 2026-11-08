"""网盘网关（infrastructure 层）：`DriveGatewayPort` 的实现。"""

from app.infrastructure.drive.gateway import (
    AdapterDriveGateway,
    DriveGatewayError,
    DriveRemoteEntry,
    gateway_for_adapter,
)

__all__ = [
    "AdapterDriveGateway",
    "DriveGatewayError",
    "DriveRemoteEntry",
    "gateway_for_adapter",
]
