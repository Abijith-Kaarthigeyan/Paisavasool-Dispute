from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.core.workflow.triage_agent import DisputeTriageAgent
from src.data.models.postgres.case import DisputeCase
from src.data.models.postgres.dispute import Dispute


def _normalize_invoice_number(invoice_number: str) -> str:
    return DisputeTriageAgent.normalize_invoice_number(invoice_number)


class DisputeRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, dispute_id: UUID) -> Dispute | None:
        result = await self.db.execute(
            select(Dispute)
            .options(
                joinedload(Dispute.case),
                joinedload(Dispute.sla),
            )
            .where(
                Dispute.id == dispute_id,
                Dispute.is_deleted.is_(False),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_dispute_number(self, dispute_number: str) -> Dispute | None:
        result = await self.db.execute(
            select(Dispute).where(
                Dispute.dispute_number == dispute_number,
                Dispute.is_deleted.is_(False),
            )
        )
        return result.scalar_one_or_none()

    async def exists(self, dispute_id: UUID) -> bool:
        result = await self.db.execute(
            select(Dispute.id).where(
                Dispute.id == dispute_id,
                Dispute.is_deleted.is_(False),
            )
        )
        return result.scalar_one_or_none() is not None

    async def count_disputes(self) -> int:
        result = await self.db.execute(
            select(func.count())
            .select_from(Dispute)
            .where(Dispute.is_deleted.is_(False))
        )
        return result.scalar() or 0

    async def count_active_disputes_for_associate(self, associate_id: UUID) -> int:
        result = await self.db.execute(
            select(func.count(Dispute.id)).where(
                Dispute.assigned_to == associate_id,
                Dispute.status.not_in(["RESOLVED", "CLOSED", "FAILED"]),
                Dispute.is_deleted.is_(False),
            )
        )
        return result.scalar() or 0

    async def list_disputes_by_case_id(self, case_id: UUID) -> list[Dispute]:
        result = await self.db.execute(
            select(Dispute).where(
                Dispute.case_id == case_id,
                Dispute.is_deleted.is_(False),
            )
        )
        return list(result.scalars().all())

    async def list_disputes(
        self,
        *,
        customer_id: UUID | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Dispute]:
        query = select(Dispute).where(Dispute.is_deleted.is_(False))
        if customer_id:
            query = query.where(Dispute.customer_id == customer_id)
        if status:
            query = query.where(Dispute.status == status)

        query = query.order_by(Dispute.created_at.desc()).limit(limit).offset(offset)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def create_dispute(
        self,
        *,
        dispute_number: str,
        case_id: UUID,
        invoice_id: UUID,
        invoice_number: str,
        customer_id: UUID,
        dispute_category: str,
        status: str = "OPEN",
        assigned_to: UUID | None = None,
        manager_id: UUID | None = None,
        opened_at: datetime | None = None,
    ) -> Dispute:
        dispute = Dispute(
            dispute_number=dispute_number,
            case_id=case_id,
            invoice_id=invoice_id,
            invoice_number=invoice_number,
            customer_id=customer_id,
            dispute_category=dispute_category,
            status=status,
            assigned_to=assigned_to,
            manager_id=manager_id,
            opened_at=opened_at or datetime.now(),
        )
        self.db.add(dispute)
        if not self.db.sync_session._flushing:
            await self.db.flush()
        return dispute

    async def update_dispute(self, dispute: Dispute) -> Dispute:
        if not self.db.sync_session._flushing:
            await self.db.flush()
        return dispute

    async def find_active_dispute_by_invoice_and_category(
        self,
        invoice_number: str,
        dispute_category: str,
        active_statuses: list[str],
        exclude_dispute_id: UUID | None = None,
    ) -> Dispute | None:
        """Find an active dispute matching invoice number and category with specific statuses."""
        query = select(Dispute).where(
            Dispute.invoice_number == invoice_number,
            Dispute.dispute_category == dispute_category,
            Dispute.status.in_(active_statuses),
            Dispute.is_deleted.is_(False),
        )
        if exclude_dispute_id:
            query = query.where(Dispute.id != exclude_dispute_id)

        result = await self.db.execute(query)
        return result.scalars().first()

    async def find_active_disputes_by_invoice_number(
        self,
        invoice_number: str,
        active_statuses: list[str],
        exclude_dispute_id: UUID | None = None,
    ) -> list[Dispute]:
        """Find active disputes whose normalized invoice number matches the target."""
        target = _normalize_invoice_number(invoice_number)
        if not target:
            return []

        query = select(Dispute).where(
            Dispute.status.in_(active_statuses),
            Dispute.is_deleted.is_(False),
        )
        if exclude_dispute_id:
            query = query.where(Dispute.id != exclude_dispute_id)

        result = await self.db.execute(query)
        disputes = list(result.scalars().all())
        return [
            dispute
            for dispute in disputes
            if _normalize_invoice_number(dispute.invoice_number) == target
        ]

    async def find_waiting_customer_disputes_by_email(
        self,
        customer_email: str,
    ) -> list[Dispute]:
        """Find WAITING_CUSTOMER disputes for the same customer email (newest first)."""
        normalized_email = (customer_email or "").strip().lower()
        if not normalized_email:
            return []

        query = (
            select(Dispute)
            .join(DisputeCase, Dispute.case_id == DisputeCase.id)
            .options(joinedload(Dispute.case))
            .where(
                Dispute.status == "WAITING_CUSTOMER",
                Dispute.is_deleted.is_(False),
                DisputeCase.is_deleted.is_(False),
            )
            .order_by(Dispute.updated_at.desc())
        )
        result = await self.db.execute(query)
        disputes = list(result.scalars().all())
        return [
            dispute
            for dispute in disputes
            if (dispute.case.customer_email or "").strip().lower() == normalized_email
        ]
