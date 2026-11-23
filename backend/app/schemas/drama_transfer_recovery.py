from typing import Literal

from pydantic import BaseModel, Field


class DramaTransferRecoveryAction(BaseModel):
    action: Literal["retry", "abandon", "abandon_directory"]
    version: str = Field(min_length=1, max_length=32)
    confirm: bool = False
    directory: str | None = Field(default=None, min_length=1, max_length=1024)


class ConfirmedDramaFile(BaseModel):
    fid: str
    name: str
    target_name: str


class DramaTransferRecoveryOut(BaseModel):
    version: str
    reason: str
    attempts: int
    requires_confirmation: bool
    can_retry: bool
    pending_names: list[str]
    output_fids: list[str]
    fallback_dirs: list[str]
    paused_fallback_dirs: list[str]
    excluded_fallback_dirs: list[str]
    discardable_fallback_dirs: list[str]
    confirmed_count: int
    confirmed_files: list[ConfirmedDramaFile]
