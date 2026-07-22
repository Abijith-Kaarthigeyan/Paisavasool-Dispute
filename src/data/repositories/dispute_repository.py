from datetime import datetime
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.core.workflow.triage_agent import DisputeTriageAgent
from src.data.models.postgres.case import DisputeCase
from src.data.models.postgres.dispute import Dispute
from src.data.models.postgres.sla import DisputeSLA


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

    def _apply_list_filters(
        self,
        query,
        *,
        customer_id: UUID | None = None,
        status: str | None = None,
        statuses: list[str] | tuple[str, ...] | None = None,
        category: str | None = None,
        invoice_number: str | None = None,
        assigned_to: UUID | None = None,
        search: str | None = None,
        sla_status: str | None = None,
        has_assignee: bool | None = None,
        exclude_statuses: list[str] | None = None,
    ):
        if customer_id:
            query = query.where(Dispute.customer_id == customer_id)
        if status:
            query = query.where(Dispute.status == status)
        if statuses:
            query = query.where(Dispute.status.in_(statuses))
        if exclude_statuses:
            query = query.where(Dispute.status.not_in(exclude_statuses))
        if category:
            query = query.where(Dispute.dispute_category == category)
        if invoice_number:
            query = query.where(Dispute.invoice_number == invoice_number)
        if assigned_to:
            query = query.where(Dispute.assigned_to == assigned_to)
        if has_assignee is True:
            query = query.where(Dispute.assigned_to.is_not(None))
        elif has_assignee is False:
            query = query.where(Dispute.assigned_to.is_(None))
        if search and search.strip():
            term = f"%{search.strip()}%"
            query = query.where(
                or_(
                    Dispute.dispute_number.ilike(term),
                    Dispute.invoice_number.ilike(term),
                )
            )
        if sla_status:
            query = query.join(Dispute.sla).where(
                DisputeSLA.status == sla_status,
                DisputeSLA.is_deleted.is_(False),
            )
        return query

    async def list_disputes(
        self,
        *,
        customer_id: UUID | None = None,
        status: str | None = None,
        statuses: list[str] | tuple[str, ...] | None = None,
        category: str | None = None,
        invoice_number: str | None = None,
        assigned_to: UUID | None = None,
        search: str | None = None,
        sla_status: str | None = None,
        has_assignee: bool | None = None,
        exclude_statuses: list[str] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Dispute]:
        query = (
            select(Dispute)
            .options(joinedload(Dispute.sla))
            .where(Dispute.is_deleted.is_(False))
        )
        query = self._apply_list_filters(
            query,
            customer_id=customer_id,
            status=status,
            statuses=statuses,
            category=category,
            invoice_number=invoice_number,
            assigned_to=assigned_to,
            search=search,
            sla_status=sla_status,
            has_assignee=has_assignee,
            exclude_statuses=exclude_statuses,
        )
        query = query.order_by(Dispute.created_at.desc()).limit(limit).offset(offset)
        result = await self.db.execute(query)
        return list(result.scalars().unique().all())

    async def list_by_assigned_associate(
        self,
        associate_id: UUID,
        *,
        customer_id: UUID | None = None,
        status: str | None = None,
        category: str | None = None,
        invoice_number: str | None = None,
        search: str | None = None,
        sla_status: str | None = None,
        exclude_statuses: list[str] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Dispute]:
        """List disputes assigned to a specific finance associate."""
        query = (
            select(Dispute)
            .options(joinedload(Dispute.sla))
            .where(
                Dispute.assigned_to == associate_id,
                Dispute.is_deleted.is_(False),
            )
        )
        query = self._apply_list_filters(
            query,
            customer_id=customer_id,
            status=status,
            category=category,
            invoice_number=invoice_number,
            search=search,
            sla_status=sla_status,
            exclude_statuses=exclude_statuses,
        )
        query = query.order_by(Dispute.created_at.desc()).limit(limit).offset(offset)
        result = await self.db.execute(query)
        return list(result.scalars().unique().all())

    async def list_distinct_invoice_numbers(self, limit: int = 500) -> list[str]:
        result = await self.db.execute(
            select(Dispute.invoice_number)
            .where(Dispute.is_deleted.is_(False))
            .distinct()
            .order_by(Dispute.invoice_number)
            .limit(limit)
        )
        return [row[0] for row in result.all() if row[0]]

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
