from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.data.clients.postgres_client import Base


class Dispute(Base):
    __tablename__ = "disputes"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    dispute_number: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, index=True
    )
    case_id: Mapped[UUID] = mapped_column(
        ForeignKey("dispute_cases.id"), nullable=False
    )
    invoice_id: Mapped[UUID] = mapped_column(nullable=False)
    invoice_number: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    customer_id: Mapped[UUID] = mapped_column(nullable=False)
    dispute_category: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="OPEN", nullable=False)
    resolution_outcome: Mapped[str | None] = mapped_column(String(50), nullable=True)

    assigned_to: Mapped[UUID | None] = mapped_column(nullable=True)
    manager_id: Mapped[UUID | None] = mapped_column(nullable=True)

    opened_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

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

    case = relationship("DisputeCase", back_populates="disputes")
    assignments = relationship(
        "DisputeAssignment", back_populates="dispute", cascade="all, delete-orphan"
    )
    comments = relationship(
        "DisputeComment", back_populates="dispute", cascade="all, delete-orphan"
    )
    attachments = relationship(
        "DisputeAttachment", back_populates="dispute", cascade="all, delete-orphan"
    )
    activities = relationship(
        "DisputeActivity", back_populates="dispute", cascade="all, delete-orphan"
    )
    sla = relationship(
        "DisputeSLA",
        back_populates="dispute",
        uselist=False,
        cascade="all, delete-orphan",
    )
    escalations = relationship(
        "DisputeEscalation", back_populates="dispute", cascade="all, delete-orphan"
    )
    workflow_context = relationship(
        "DisputeWorkflowContext",
        back_populates="dispute",
        uselist=False,
        cascade="all, delete-orphan",
    )
    recommendations = relationship(
        "DisputeResolutionRecommendation",
        back_populates="dispute",
        cascade="all, delete-orphan",
    )
    agent_runs = relationship(
        "DisputeAgentRun", back_populates="dispute", cascade="all, delete-orphan"
    )
    evidence_snapshots = relationship(
        "DisputeEvidenceSnapshot",
        back_populates="dispute",
        cascade="all, delete-orphan",
    )
    review_queue_items = relationship(
        "DisputeReviewQueue", back_populates="dispute", cascade="all, delete-orphan"
    )
    communications = relationship(
        "DisputeCommunication", back_populates="dispute", cascade="all, delete-orphan"
    )
