from uuid import UUID

from src.core.services.audit_service import AuditService
from src.data.repositories.communication_repository import CommunicationRepository
from src.data.repositories.dispute_repository import DisputeRepository
from src.data.repositories.other_repositories import CommentRepository


class CorrelationService:
    # Collapse granular types to AMENDMENT
    COLLAPSED_CATEGORIES = {
        "PRICING",
        "TAX",
        "QUANTITY",
        "WRONG_PRODUCT",
        "WRONG_CUSTOMER",
        "WRONG_INVOICE_DATE",
        "WRONG_DUE_DATE",
        "WRONG_TOTAL",
        "AMENDMENT",
    }

    ACTIVE_STATUSES = [
        "OPEN",
        "IN_REVIEW",
        "WAITING_CUSTOMER",
        "WAITING_INTERNAL_TEAM",
        "WAITING_ASSOCIATE_APPROVAL",
        "WAITING_PAYMENT_REVIEW",
    ]

    SYSTEM_USER_ID = UUID("00000000-0000-0000-0000-000000000001")

    def __init__(
        self,
        dispute_repo: DisputeRepository,
        communication_repo: CommunicationRepository,
        comment_repo: CommentRepository,
        audit_service: AuditService,
    ):
        self.dispute_repo = dispute_repo
        self.communication_repo = communication_repo
        self.comment_repo = comment_repo
        self.audit_service = audit_service

    def collapse_category(self, category: str) -> str:
        """Collapses granular dispute categories into AMENDMENT or leaves them independent."""
        upper_cat = category.upper().strip()
        if upper_cat in self.COLLAPSED_CATEGORIES:
            return "AMENDMENT"
        return upper_cat

    async def correlate_dispute(
        self,
        *,
        invoice_number: str,
        raw_category: str,
        customer_email: str,
        email_subject: str,
        email_body: str,
        exclude_dispute_id: UUID | None = None,
    ) -> UUID | None:
        """Correlates an incoming dispute detail against active dispute workflows.

        If a match is found, attaches the email as a communication/comment and returns the dispute ID.
        If no match is found, returns None (caller should create a new dispute workflow).
        """
        collapsed_cat = self.collapse_category(raw_category)

        # 1. Search for active dispute matching invoice number and collapsed category
        matched_dispute = await self.dispute_repo.find_active_dispute_by_invoice_and_category(
            invoice_number=invoice_number,
            dispute_category=collapsed_cat,
            active_statuses=self.ACTIVE_STATUSES,
            exclude_dispute_id=exclude_dispute_id,
        )

        if not matched_dispute:
            return None

        # 2. Match found: attach communication
        comm = await self.communication_repo.create_communication(
            dispute_id=matched_dispute.id,
            recipient=customer_email,
            subject=email_subject,
            body=email_body,
            communication_type="CUSTOMER",
        )

        # 3. Add system comment to dispute timeline
        comment_text = f"Correlated & attached communication: '{email_subject}' from {customer_email}."
        created_by = matched_dispute.assigned_to or self.SYSTEM_USER_ID

        await self.comment_repo.create_comment(
            dispute_id=matched_dispute.id,
            comment=comment_text,
            comment_type="CUSTOMER",
            created_by=created_by,
        )

        # 4. Log audit trails
        await self.audit_service.log_event(
            dispute_id=matched_dispute.id,
            action="COMMENT_ADDED",
            performed_by=created_by,
            metadata={"comment_id": str(comm.id), "source": "correlation"},
        )

        return matched_dispute.id
