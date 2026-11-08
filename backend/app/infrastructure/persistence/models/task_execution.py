from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.persistence.base import Base


class TaskExecution(Base):
    __tablename__ = "task_executions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tree_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    run_log: Mapped[str | None] = mapped_column(Text, nullable=True)
    adapter_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # 不再 eager loading 父任务：执行记录在列表/详情里都只用 task_id，
    # 之前 lazy="selectin" 会让每次查询执行记录都额外再查一遍 tasks。
    task = relationship("Task", back_populates="executions")
