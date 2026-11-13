"""Value objects for drive operations."""
from dataclasses import dataclass

@dataclass(frozen=True)
class DriveCapabilities:
    supports_share: bool = False
    supports_result_lookup: bool = False
    supports_lead_in: bool = False
    supports_own_material: bool = False

@dataclass(frozen=True)
class ShareLinkResult:
    url: str = ''
    share_id: str = ''
    title: str = ''
    passcode: str = ''
    expires_in_days: int | None = None
    supported: bool = False

@dataclass(frozen=True)
class DriveFile:
    id: str
    name: str
    is_dir: bool = False
    size: int = 0
    modified_at: object = None
    token: str | None = None

@dataclass(frozen=True)
class TransferRequest:
    share_id: str
    share_token: str
    target_dir: str
    files: tuple[DriveFile, ...]

@dataclass(frozen=True)
class SavedFile:
    source_id: str
    id: str
    name: str

@dataclass(frozen=True)
class TransferResult:
    state: str
    files: tuple[SavedFile, ...] = ()
    task_id: str = ''
    kind: str = 'unknown'
    retryable: bool = False
    confirmed: bool = False
    message: str = ''
