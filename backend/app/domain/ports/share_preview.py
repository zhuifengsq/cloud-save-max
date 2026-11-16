"""Detached preview inputs and read-only IO capabilities; no ORM or transport."""
from dataclasses import dataclass
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class SharePreviewRead:
    share_id: str
    folder_id: str
    author: str | None
    items: tuple[Mapping[str, Any], ...]
    destination_names: tuple[str, ...] = ()


class SharePreviewReaderPort(Protocol):
    drive_type: str
    account_name: str

    def read(self, shareurl: str, folder_id: str | None, destination: str) -> SharePreviewRead: ...


class PreviewRenamerPort(Protocol):
    """Existing in-memory naming rules; this capability performs no IO."""
    def set_taskname(self, taskname: str) -> None: ...
    def magic_regex_conv(self, pattern: str, replace: str) -> tuple[str, str]: ...
    def sub(self, pattern: str, replace: str, file_name: str) -> str: ...
    def is_exists(self, filename: str, filename_list: list[str], ignore_ext: bool = False) -> str | None: ...
