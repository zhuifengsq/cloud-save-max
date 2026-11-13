"""网盘网关端口：业务层看到的最小能力集。

目标架构的稳定接口，不急于套用。现有 10 家适配器（Quark/Baidu/Ali/115/123/UC/
Xunlei/189/139/Guangya）已具备同名能力，不需要继承本协议（structural typing）。

注意：transfer 是外部副作用，调用方负责幂等键与「以网盘侧为准」的事实源判定，
端口本身不提供重试语义（见 docs/architecture-evolution-plan.md §3.6 规则 6）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable
from app.domain.drive_types import DriveCapabilities, ShareLinkResult, TransferRequest, TransferResult


@dataclass(frozen=True)
class ShareProbeResult:
    """一次「分享链接是否还能打开」的探测结论。

    valid：目录能正常读取（唯一能判定「链接有效」的情况）。
    kind：与适配器 ``classify_share_failure`` 同一套词表（invalid/passcode/captcha/auth/throttled/pending/unknown）。
    stage：失败发生在哪一步——parse（链接解析不出分享 id）/ verify（取 stoken 失败）/ read（读目录失败）。
        调用方据此选择面向用户的兜底文案，端口本身不产出用户文案。
    """

    valid: bool
    kind: str = "unknown"
    message: str = ""
    stage: str = "verify"


@runtime_checkable
class RemoteEntry(Protocol):
    """目录项只读视图，避免把适配器内部结构泄露给业务层。"""

    name: str
    is_dir: bool
    size: int


@runtime_checkable
class DriveGatewayPort(Protocol):
    """能力最小集：列目录、建目录、转存、校验分享、关闭。"""

    drive_type: str
    capabilities: DriveCapabilities

    def transfer_files(self, request: TransferRequest) -> TransferResult:
        """Submit a transfer once and report whether its result is confirmed."""

    def create_share_link(self, path: str, title: str) -> ShareLinkResult:
        """Create a share, or report that the capability is unsupported."""

    def list_dir(self, path: str) -> Sequence[RemoteEntry]:
        """列目录（适配器自己的路径语义，如 / 或根目录 id）。"""

    def create_dir(self, path: str) -> None:
        """创建目录；已存在时视为成功（幂等）。"""

    def transfer(self, share_url: str, target_dir: str, *, passcode: str = "") -> Mapping[str, Any]:
        """转存分享到目标目录；返回实现自定义的结果，至少含 success 字段。"""

    def check_share(self, share_url: str, *, passcode: str = "") -> bool:
        """校验分享链接是否仍有效。"""

    def close(self) -> None:
        """释放连接/会话资源。"""
