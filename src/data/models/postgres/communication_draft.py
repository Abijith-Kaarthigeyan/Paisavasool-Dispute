from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.data.clients.postgres_client import Base


class DisputeCommunicationDraft(Base):
    __tablename__ = "dispute_communication_drafts"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    dispute_id: Mapped[UUID] = mapped_column(
        ForeignKey("disputes.id"), nullable=False, index=True
    )
    recipient: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_communication_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("dispute_communications.id"), nullable=True
    )
    trigger: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    dispute = relationship("Dispute", back_populates="communication_drafts")
    source_communication = relationship("DisputeCommunication")
