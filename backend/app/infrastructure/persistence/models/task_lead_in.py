"""追剧任务引流文件写入记录：单任务 + 单网盘只保留最近一次结果。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.persistence.base import Base


class TaskLeadInRecord(Base):
    __tablename__ = "task_lead_in_records"
    __table_args__ = (
        UniqueConstraint("task_id", "drive_type", name="uq_task_lead_in_records_task_drive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), index=True, nullable=False)
    drive_type: Mapped[str] = mapped_column(String(32), nullable=False)
    account_name: Mapped[str] = mapped_column(String(128), nullable=False, server_default="")
    dir_path: Mapped[str] = mapped_column(String(512), nullable=False, server_default="")
    material_name: Mapped[str] = mapped_column(String(512), nullable=False, server_default="")
    material_fid: Mapped[str] = mapped_column(String(256), nullable=False, server_default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    message: Mapped[str] = mapped_column(String(512), nullable=False, server_default="")
    config_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
