"""Sharing use-case boundaries: values and capabilities, no ORM or transport."""
from dataclasses import dataclass, field
from typing import Mapping, Protocol


@dataclass(frozen=True)
class TaskSharePlan:
    task_id: int
    taskname: str
    savepath: str
    shareurl: str
    task_account_name: str | None
    account_name: str
    drive_type: str
    config: Mapping[str, object] = field(repr=False)
    cookie: str = field(default='', repr=False)


class CreatedShare(Protocol):
    url: str
    share_id: str
    title: str
    passcode: str
    expires_in_days: int | None
    supported: bool


class ShareCapabilities(Protocol):
    supports_share: bool


class TaskShareGatewayPort(Protocol):
    @property
    def capabilities(self) -> ShareCapabilities: ...

    def create_share_link(self, path: str, title: str) -> CreatedShare: ...


class TaskShareStorePort(Protocol):
    def prepare(self, task_id: int) -> TaskSharePlan: ...

    def save(self, plan: TaskSharePlan, result: CreatedShare) -> str: ...
