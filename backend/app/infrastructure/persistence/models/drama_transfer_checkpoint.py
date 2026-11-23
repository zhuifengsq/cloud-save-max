from sqlalchemy import ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.persistence.base import Base


class DramaTransferCheckpoint(Base):
    __tablename__ = "drama_transfer_checkpoints"

    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    batch: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    fallback_dirs: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    fallback_missing: Mapped[dict[str, int]] = mapped_column(JSON, server_default="{}", nullable=False)
    fallback_scope: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fallback_excluded: Mapped[list[str]] = mapped_column(JSON, server_default="[]", nullable=False)
    confirmed: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    renames_started: Mapped[bool] = mapped_column(nullable=False)
    version: Mapped[str] = mapped_column(String(32), server_default="legacy", nullable=False)
    recovery: Mapped[dict] = mapped_column(JSON, server_default="{}", nullable=False)
