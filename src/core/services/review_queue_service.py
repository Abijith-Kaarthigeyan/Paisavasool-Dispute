import logging
from uuid import UUID

from src.core.exceptions.business_exceptions import (
    DisputeNotFoundException,
    ForbiddenException,
    ReviewQueueItemNotFoundException,
)
from src.core.workflow.resume_service import DisputeResumeService
from src.core.workflow.triage_agent import DisputeTriageAgent
from src.data.clients.ar_service_client import ARServiceClient
from src.data.models.postgres.review_queue import DisputeReviewQueue
from src.data.repositories.dispute_repository import DisputeRepository
from src.data.repositories.other_repositories import (
    ActivityRepository,
    CommentRepository,
)
from src.data.repositories.review_queue_repository import ReviewQueueRepository
from src.schemas.auth import RoleName

logger = logging.getLogger(__name__)


class ReviewQueueService:
    def __init__(
        self,
        review_repo: ReviewQueueRepository,
        dispute_repo: DisputeRepository,
        comment_repo: CommentRepository,
        activity_repo: ActivityRepository,
        ar_client: ARServiceClient,
        resume_service: DisputeResumeService,
    ):
        self.review_repo = review_repo
        self.dispute_repo = dispute_repo
        self.comment_repo = comment_repo
        self.activity_repo = activity_repo
        self.ar_client = ar_client
        self.resume_service = resume_service

    async def list_review_queue(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DisputeReviewQueue]:
        return await self.review_repo.list_review_queue(
            status=status,
            limit=limit,
            offset=offset,
        )

    async def list_review_queue_for_user(
        self,
        *,
        role: RoleName,
        user_id: UUID,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DisputeReviewQueue]:
        if role == RoleName.FINANCE_ASSOCIATE:
            return await self.review_repo.list_review_queue_for_associate(
                user_id,
                status=status,
                limit=limit,
                offset=offset,
            )
        return await self.list_review_queue(
            status=status,
            limit=limit,
            offset=offset,
        )

    async def resolve_item(
        self,
        item_id: UUID,
        *,
        invoice_number: str,
        dispute_category: str,
        comments: str | None,
        performed_by: UUID,
        role: RoleName,
    ) -> dict[str, str]:
        db = self.review_repo.db
        item = await self.review_repo.get_by_id(item_id)
        if not item:
            raise ReviewQueueItemNotFoundException(
                f"Review queue item {item_id} not found."
            )

        dispute = await self.dispute_repo.get_by_id(item.dispute_id)
        if not dispute:
            raise DisputeNotFoundException(f"Dispute {item.dispute_id} not found.")
        if role == RoleName.FINANCE_ASSOCIATE and dispute.assigned_to != performed_by:
            raise ForbiddenException("Access to this dispute is denied.")

        item.status = "RESOLVED"
        await self.review_repo.update_review_queue_item(item)

        norm_cat = DisputeTriageAgent.normalize_category(dispute_category)
        dispute.invoice_number = invoice_number
        dispute.dispute_category = norm_cat
        dispute.status = "OPEN"

        invoice = await self.ar_client.lookup_invoice_by_number(invoice_number)
        if invoice:
            dispute.invoice_id = UUID(str(invoice["id"]))
            dispute.customer_id = UUID(str(invoice["customer_id"]))

        await self.dispute_repo.update_dispute(dispute)

        await self.comment_repo.create_comment(
            dispute_id=dispute.id,
            comment=f"Resolved from review queue: {comments or ''}",
            comment_type="INTERNAL",
            created_by=performed_by,
        )
        await self.activity_repo.create_activity(
            dispute_id=dispute.id,
            activity_type="REVIEW_QUEUE_RESOLVED",
            metadata={
                "invoice_number": invoice_number,
                "dispute_category": norm_cat,
            },
            performed_by=performed_by,
        )
        await db.commit()

        state_updates = {
            "requires_human_review": False,
            "review_reason": None,
            "invoice_number": dispute.invoice_number,
            "dispute_category": dispute.dispute_category,
            "invoice_id": dispute.invoice_id,
            "customer_id": dispute.customer_id,
        }

        try:
            await self.resume_service.resume_workflow(
                db=db,
                dispute_id=dispute.id,
                state_updates=state_updates,
                as_node="validation_node",
            )
        except Exception as e:
            logger.info("Resumed graph paused again: %s", str(e))

        return {
            "status": "SUCCESS",
            "message": "Dispute resolved and workflow resumed.",
        }
