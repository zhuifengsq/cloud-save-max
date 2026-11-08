from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.persistence.base import Base


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_uid: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    task_type: Mapped[str] = mapped_column(String(32), nullable=False, server_default="generic")
    taskname: Mapped[str] = mapped_column(String(255), nullable=False)
    shareurl: Mapped[str] = mapped_column(Text, nullable=False)
    savepath: Mapped[str] = mapped_column(String(255), nullable=False)
    pattern: Mapped[str | None] = mapped_column(String(255), nullable=True)
    replace: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ignore_extension: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="0")
    account_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tmdb_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tmdb_media_type: Mapped[str | None] = mapped_column(String(8), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="1")
    addition_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # 默认改为按需加载：此前 lazy="selectin" 会让任何 select(Task) 都顺带加载该任务
    # 的全部执行记录（含 run_log 文本），任务列表接口因此被放大到数 MB / 数百毫秒。
    # 需要执行记录的地方请显式 selectinload/手动装载，不要改回模型级 eager loading。
    executions = relationship("TaskExecution", back_populates="task", cascade="all, delete-orphan")
