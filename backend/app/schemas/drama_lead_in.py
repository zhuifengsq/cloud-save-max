"""追剧任务引流文件相关响应/请求模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class TaskLeadInSummaryOut(BaseModel):
    """任务列表/详情里的引流摘要，不包含任何链接或提取码。"""

    state: str = "none"
    last_added_at: datetime | None = None


class DramaLeadInDriveOut(BaseModel):
    drive_type: str
    drive_name: str = ""
    supported: bool = False
    own_file_supported: bool = False
    own_file_browse_supported: bool = False
    reason: str = ""
    configured: bool = False
    mode: str = "share"
    account_name: str = ""
    file_path: str = ""
    file_size: int | None = None
    shareurl: str = ""
    passcode_set: bool = False
    file_fid: str = ""
    file_name: str = ""
    config_version: int = 0
    updated_at: datetime | None = None


class DramaLeadInSettingsOut(BaseModel):
    enabled: bool = False
    version: int = 1
    drives: list[DramaLeadInDriveOut] = []


class DramaLeadInMaterialIn(BaseModel):
    drive_type: str = Field(min_length=1, max_length=32)
    mode: str = Field(default="share", max_length=16)
    account_name: str = Field(default="", max_length=128)
    file_path: str = Field(default="", max_length=2048)
    shareurl: str = Field(default="", max_length=2048)
    passcode: str = Field(default="", max_length=64)
    file_fid: str = Field(default="", max_length=256)
    file_name: str = Field(default="", max_length=512)


class DramaLeadInSettingsUpdateIn(BaseModel):
    enabled: bool = False
    materials: list[DramaLeadInMaterialIn] = []


class DramaLeadInScanIn(BaseModel):
    drive_type: str = Field(min_length=1, max_length=32)
    shareurl: str = Field(default="", max_length=2048)
    passcode: str = Field(default="", max_length=64)


class DramaLeadInScanFileOut(BaseModel):
    fid: str = ""
    name: str = ""
    size: int | None = None
    dir: bool = False
    path: str = ""


class DramaLeadInScanOut(BaseModel):
    files: list[DramaLeadInScanFileOut] = []
    selected_fid: str = ""


class DramaLeadInOwnFileResolveIn(BaseModel):
    drive_type: str = Field(min_length=1, max_length=32)
    account_name: str = Field(min_length=1, max_length=128)
    file_path: str = Field(min_length=1, max_length=2048)


class DramaLeadInOwnFileOut(BaseModel):
    fid: str = ""
    name: str = ""
    path: str = ""
    size: int | None = None
    md5: str = ""
    dir: bool = False


class DramaLeadInOwnFileResolveOut(BaseModel):
    file: DramaLeadInOwnFileOut = Field(default_factory=DramaLeadInOwnFileOut)


class DramaLeadInOwnDirIn(BaseModel):
    drive_type: str = Field(min_length=1, max_length=32)
    account_name: str = Field(min_length=1, max_length=128)
    dir_path: str = Field(default="", max_length=2048)


class DramaLeadInOwnDirEntryOut(BaseModel):
    fid: str = ""
    name: str = ""
    dir: bool = False
    size: int | None = None
    path: str = ""


class DramaLeadInOwnDirOut(BaseModel):
    path: str = "/"
    fid: str = ""
    entries: list[DramaLeadInOwnDirEntryOut] = []


class DramaLeadInPreviewDriveOut(BaseModel):
    drive_type: str = ""
    drive_name: str = ""
    supported: bool = False


class DramaLeadInPreviewAccountOut(BaseModel):
    account_name: str = ""
    nickname: str = ""


class DramaLeadInPreviewDirectoryOut(BaseModel):
    path: str = ""
    fid: str = ""
    exists: bool = False


class DramaLeadInPreviewMaterialOut(BaseModel):
    name: str = ""
    fid: str = ""
    source: str = "share"
    account_name: str = ""
    path: str = ""


class DramaLeadInMaterialOut(BaseModel):
    """确认结果里的素材信息，保持与旧版本一致的两个字段。"""

    name: str = ""
    fid: str = ""


class DramaLeadInPreviewOut(BaseModel):
    available: bool = False
    reason: str = ""
    drive: DramaLeadInPreviewDriveOut = Field(default_factory=DramaLeadInPreviewDriveOut)
    account: DramaLeadInPreviewAccountOut | None = None
    directory: DramaLeadInPreviewDirectoryOut | None = None
    material: DramaLeadInPreviewMaterialOut | None = None
    state: str = "absent"
    state_message: str = ""
    last_added_at: datetime | None = None
    preview_token: str = ""
    expires_at: datetime | None = None


class DramaLeadInConfirmIn(BaseModel):
    preview_token: str = Field(min_length=1, max_length=4096)


class DramaLeadInConfirmOut(BaseModel):
    status: str
    message: str = ""
    added_at: datetime | None = None
    record_id: int = 0
    material: DramaLeadInMaterialOut = Field(default_factory=DramaLeadInMaterialOut)
    directory: DramaLeadInPreviewDirectoryOut = Field(default_factory=DramaLeadInPreviewDirectoryOut)


class DramaLeadInRecordOut(BaseModel):
    state: str = "none"
    message: str = ""
    last_added_at: datetime | None = None
    material_name: str = ""
