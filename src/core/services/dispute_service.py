from datetime import datetime
from uuid import UUID

from src.core.exceptions.business_exceptions import (
    DisputeNotFoundException,
    ForbiddenException,
    SLADetailsNotFoundException,
)
from src.core.services.sla_service import SLAService
from src.core.services.workflow_context_service import WorkflowContextService
from src.data.models.postgres.activity import DisputeActivity
from src.data.models.postgres.comment import DisputeComment
from src.data.models.postgres.communication import DisputeCommunication
from src.data.models.postgres.dispute import Dispute
from src.data.models.postgres.evidence_snapshot import DisputeEvidenceSnapshot
from src.data.models.postgres.sla import DisputeSLA
from src.data.models.postgres.workflow_context import DisputeWorkflowContext
from src.data.repositories.communication_repository import CommunicationRepository
from src.data.repositories.dispute_repository import DisputeRepository
from src.data.repositories.other_repositories import (
    ActivityRepository,
    CommentRepository,
    EvidenceSnapshotRepository,
)
from src.data.repositories.sla_repository import SLARepository
from src.schemas.auth import RoleName


class DisputeService:
    def __init__(
        self,
        dispute_repo: DisputeRepository,
        activity_repo: ActivityRepository,
        comment_repo: CommentRepository,
        comm_repo: CommunicationRepository,
        sla_repo: SLARepository,
        workflow_context_service: WorkflowContextService,
        evidence_repo: EvidenceSnapshotRepository,
        sla_service: SLAService | None = None,
    ):
        self.dispute_repo = dispute_repo
        self.activity_repo = activity_repo
        self.comment_repo = comment_repo
        self.comm_repo = comm_repo
        self.sla_repo = sla_repo
        self.workflow_context_service = workflow_context_service
        self.evidence_repo = evidence_repo
        self.sla_service = sla_service

    async def list_disputes(
        self,
        *,
        customer_id: UUID | None = None,
        status: str | None = None,
        category: str | None = None,
        invoice_number: str | None = None,
        assigned_to: UUID | None = None,
        search: str | None = None,
        sla_status: str | None = None,
        has_assignee: bool | None = None,
        exclude_statuses: list[str] | None = None,
        created_at_from: datetime | None = None,
        created_at_to: datetime | None = None,
        opened_at_from: datetime | None = None,
        opened_at_to: datetime | None = None,
        sort_by: str | None = None,
        sort_order: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[Dispute], int]:
        return await self.dispute_repo.list_disputes(
            customer_id=customer_id,
            status=status,
            category=category,
            invoice_number=invoice_number,
            assigned_to=assigned_to,
            search=search,
            sla_status=sla_status,
            has_assignee=has_assignee,
            exclude_statuses=exclude_statuses,
            created_at_from=created_at_from,
            created_at_to=created_at_to,
            opened_at_from=opened_at_from,
            opened_at_to=opened_at_to,
            sort_by=sort_by,
            sort_order=sort_order,
            limit=limit,
            offset=offset,
        )

    async def list_disputes_for_user(
        self,
        *,
        role: RoleName,
        user_id: UUID,
        customer_id: UUID | None = None,
        status: str | None = None,
        category: str | None = None,
        invoice_number: str | None = None,
        assigned_to: UUID | None = None,
        search: str | None = None,
        sla_status: str | None = None,
        has_assignee: bool | None = None,
        exclude_statuses: list[str] | None = None,
        created_at_from: datetime | None = None,
        created_at_to: datetime | None = None,
        opened_at_from: datetime | None = None,
        opened_at_to: datetime | None = None,
        sort_by: str | None = None,
        sort_order: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[Dispute], int]:
        if role == RoleName.FINANCE_ASSOCIATE:
            return await self.dispute_repo.list_by_assigned_associate(
                user_id,
                customer_id=customer_id,
                status=status,
                category=category,
                invoice_number=invoice_number,
                search=search,
                sla_status=sla_status,
                exclude_statuses=exclude_statuses,
                created_at_from=created_at_from,
                created_at_to=created_at_to,
                opened_at_from=opened_at_from,
                opened_at_to=opened_at_to,
                sort_by=sort_by,
                sort_order=sort_order,
                limit=limit,
                offset=offset,
            )
        return await self.list_disputes(
            customer_id=customer_id,
            status=status,
            category=category,
            invoice_number=invoice_number,
            assigned_to=assigned_to,
            search=search,
            sla_status=sla_status,
            has_assignee=has_assignee,
            exclude_statuses=exclude_statuses,
            created_at_from=created_at_from,
            created_at_to=created_at_to,
            opened_at_from=opened_at_from,
            opened_at_to=opened_at_to,
            sort_by=sort_by,
            sort_order=sort_order,
            limit=limit,
            offset=offset,
        )

    async def list_assigned_disputes(self, user_id: UUID) -> list[Dispute]:
        disputes, _ = await self.dispute_repo.list_by_assigned_associate(user_id)
        return disputes

    def verify_associate_access(
        self, dispute: Dispute, role: RoleName, user_id: UUID
    ) -> None:
        if role == RoleName.FINANCE_ASSOCIATE and dispute.assigned_to != user_id:
            raise ForbiddenException("Access to this dispute is denied.")

    def assert_associate_can_mutate_status(
        self, dispute: Dispute, role: RoleName
    ) -> None:
        if role == RoleName.FINANCE_ASSOCIATE and dispute.status == "ESCALATED":
            raise ForbiddenException(
                "Dispute is escalated to the manager; associates cannot change its status."
            )

    async def get_dispute(self, dispute_id: UUID) -> Dispute:
        dispute = await self.dispute_repo.get_by_id(dispute_id)
        if not dispute:
            raise DisputeNotFoundException(f"Dispute {dispute_id} not found.")
        return dispute

    async def get_dispute_for_user(
        self, dispute_id: UUID, role: RoleName, user_id: UUID
    ) -> Dispute:
        dispute = await self.get_dispute(dispute_id)
        self.verify_associate_access(dispute, role, user_id)
        return dispute

    async def list_activities(self, dispute_id: UUID) -> list[DisputeActivity]:
        return await self.activity_repo.list_activities_for_dispute(dispute_id)

    async def list_comments(self, dispute_id: UUID) -> list[DisputeComment]:
        return await self.comment_repo.list_comments_for_dispute(dispute_id)

    async def create_comment(
        self,
        *,
        dispute_id: UUID,
        comment: str,
        comment_type: str,
        created_by: UUID,
    ) -> DisputeComment:
        await self.get_dispute(dispute_id)
        return await self.comment_repo.create_comment(
            dispute_id=dispute_id,
            comment=comment,
            comment_type=comment_type,
            created_by=created_by,
        )

    async def get_workflow_context(self, dispute_id: UUID) -> DisputeWorkflowContext:
        return await self.workflow_context_service.get_context(dispute_id)

    async def list_communications(self, dispute_id: UUID) -> list[DisputeCommunication]:
        return await self.comm_repo.get_communications_for_dispute(dispute_id)

    async def get_sla(self, dispute_id: UUID) -> DisputeSLA:
        """Return live SLA progress. Recalculates so UI is not stuck on stale stored %."""
        if self.sla_service is not None:
            sla = await self.sla_service.calculate_progress(dispute_id)
            if sla:
                return sla

        sla = await self.sla_repo.get_by_dispute_id(dispute_id)
        if not sla:
            raise SLADetailsNotFoundException(
                f"SLA details not found for dispute {dispute_id}."
            )
        return sla

    async def list_evidence_snapshots(
        self, dispute_id: UUID
    ) -> list[DisputeEvidenceSnapshot]:
        return await self.evidence_repo.list_for_dispute(dispute_id)
