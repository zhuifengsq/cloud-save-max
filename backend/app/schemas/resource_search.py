from __future__ import annotations

from pydantic import BaseModel, Field


class TaskSuggestionItemOut(BaseModel):
    taskname: str = Field(default="", max_length=512)
    shareurl: str = Field(default="", max_length=2048)
    password: str | None = None
    content: str | None = None
    datetime: str | None = None
    channel: str | None = None
    source: str | None = None
    share_author_name: str | None = None
    is_preferred_sharer: bool = Field(default=False, description="是否为优选分享者")


class TaskSuggestionListOut(BaseModel):
    success: bool = True
    data: list[TaskSuggestionItemOut] = []
    message: str | None = None
