from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.data.clients.postgres_client import Base


class DisputeWorkflowContext(Base):
    __tablename__ = "dispute_workflow_context"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    dispute_id: Mapped[UUID] = mapped_column(ForeignKey("disputes.id"), nullable=False)
    workflow_name: Mapped[str] = mapped_column(String(100), nullable=False)
    current_node: Mapped[str] = mapped_column(String(100), nullable=False)
    workflow_state: Mapped[dict] = mapped_column(JSON, nullable=False)
    last_checkpoint: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Soft Delete / Data Retention
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_by: Mapped[UUID | None] = mapped_column(nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    dispute = relationship("Dispute", back_populates="workflow_context")
