from pydantic import BaseModel, Field


class SavedLatestProgressOut(BaseModel):
    episode: int = Field(ge=0)
    season: int | None = None
    name: str
    at: str


class SavedProgressCorrectionOut(BaseModel):
    task_id: int
    previous_episode: int | None
    saved_latest: SavedLatestProgressOut
    scanned_files: int
    scanned_directories: int
