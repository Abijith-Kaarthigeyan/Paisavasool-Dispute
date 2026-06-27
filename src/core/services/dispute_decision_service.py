import contextlib
from uuid import UUID

from src.core.exceptions.business_exceptions import (
    DisputeNotFoundException,
    ValidationException,
)
from src.core.services.recommendation_service import RecommendationService
from src.core.workflow.resume_service import DisputeResumeService
from src.data.repositories.dispute_repository import DisputeRepository
from src.data.repositories.other_repositories import (
    ActivityRepository,
    CommentRepository,
)


class DisputeDecisionService:
    def __init__(
        self,
        dispute_repo: DisputeRepository,
        comment_repo: CommentRepository,
        activity_repo: ActivityRepository,
        recommendation_service: RecommendationService,
        resume_service: DisputeResumeService,
    ):
        self.dispute_repo = dispute_repo
        self.comment_repo = comment_repo
        self.activity_repo = activity_repo
        self.recommendation_service = recommendation_service
        self.resume_service = resume_service

    async def _require_dispute(self, dispute_id: UUID):
        dispute = await self.dispute_repo.get_by_id(dispute_id)
        if not dispute:
            raise DisputeNotFoundException(f"Dispute {dispute_id} not found.")
        return dispute

    async def _record_decision(
        self,
        *,
        dispute_id: UUID,
        comment_text: str,
        activity_type: str,
        decision: str,
        comments: str | None,
        performed_by: UUID,
    ) -> None:
        await self.comment_repo.create_comment(
            dispute_id=dispute_id,
            comment=comment_text,
            comment_type="INTERNAL",
            created_by=performed_by,
        )
        await self.activity_repo.create_activity(
            dispute_id=dispute_id,
            activity_type=activity_type,
            metadata={"decision": decision, "comments": comments or ""},
            performed_by=performed_by,
        )

    async def submit_associate_decision(
        self,
        dispute_id: UUID,
        *,
        decision: str,
        comments: str | None,
        amended_invoice_json: dict | None,
        performed_by: UUID,
    ) -> dict[str, str]:
        db = self.dispute_repo.db
        normalized = decision.upper()
        if normalized not in ["APPROVE", "REJECT", "EDIT_AND_APPLY"]:
            raise ValidationException(
                "Invalid decision. Must be APPROVE, REJECT, or EDIT_AND_APPLY."
            )
        if normalized == "EDIT_AND_APPLY" and not amended_invoice_json:
            raise ValidationException(
                "amended_invoice_json is required for EDIT_AND_APPLY."
            )

        await self._require_dispute(dispute_id)

        if normalized == "EDIT_AND_APPLY":
            await self.recommendation_service.persist_recommendation(
                dispute_id=dispute_id,
                recommended_action=f"ASSOCIATE_EDIT_AND_APPLY: {comments or ''}",
                confidence=100.0,
                created_by_agent="ASSOCIATE",
                recommended_invoice_json=amended_invoice_json,
            )

        comment_text = f"Associate Decision: {normalized}. Comments: {comments or ''}"
        await self._record_decision(
            dispute_id=dispute_id,
            comment_text=comment_text,
            activity_type="ASSOCIATE_DECISION",
            decision=normalized,
            comments=comments,
            performed_by=performed_by,
        )
        await db.commit()

        state_updates: dict = {
            "resolution_outcome": normalized,
            "requires_human_review": False,
            "review_reason": None,
        }
        if normalized == "EDIT_AND_APPLY" and amended_invoice_json:
            state_updates["metadata"] = {
                "amended_invoice_json": amended_invoice_json,
            }
        with contextlib.suppress(Exception):
            await self.resume_service.resume_workflow(
                db=db,
                dispute_id=dispute_id,
                state_updates=state_updates,
                as_node="waiting_approval_node",
            )

        return {
            "status": "SUCCESS",
            "message": "Decision processed and workflow resumed.",
        }

    async def submit_payment_review_decision(
        self,
        dispute_id: UUID,
        *,
        decision: str,
        comments: str | None,
        performed_by: UUID,
    ) -> dict[str, str]:
        db = self.dispute_repo.db
        normalized = decision.upper()
        if normalized not in ["SETTLEMENT_DONE", "SETTLEMENT_NOT_DONE"]:
            raise ValidationException(
                "Invalid decision. Must be SETTLEMENT_DONE or SETTLEMENT_NOT_DONE."
            )

        await self._require_dispute(dispute_id)

        comment_text = (
            f"Payment Review Decision: {normalized}. Comments: {comments or ''}"
        )
        await self._record_decision(
            dispute_id=dispute_id,
            comment_text=comment_text,
            activity_type="PAYMENT_REVIEW_DECISION",
            decision=normalized,
            comments=comments,
            performed_by=performed_by,
        )
        await db.commit()

        state_updates = {
            "resolution_outcome": normalized,
            "requires_human_review": False,
            "review_reason": None,
        }
        with contextlib.suppress(Exception):
            await self.resume_service.resume_workflow(
                db=db,
                dispute_id=dispute_id,
                state_updates=state_updates,
                as_node="waiting_resolution_node",
            )

        return {
            "status": "SUCCESS",
            "message": "Decision processed and workflow resumed.",
        }

    async def submit_operational_review_decision(
        self,
        dispute_id: UUID,
        *,
        decision: str,
        comments: str | None,
        performed_by: UUID,
    ) -> dict[str, str]:
        db = self.dispute_repo.db
        normalized = decision.upper()
        if normalized not in ["ACKNOWLEDGED", "REJECTED"]:
            raise ValidationException(
                "Invalid decision. Must be ACKNOWLEDGED or REJECTED."
            )

        await self._require_dispute(dispute_id)

        comment_text = (
            f"Operational Review Decision: {normalized}. Comments: {comments or ''}"
        )
        await self._record_decision(
            dispute_id=dispute_id,
            comment_text=comment_text,
            activity_type="OPERATIONAL_REVIEW_DECISION",
            decision=normalized,
            comments=comments,
            performed_by=performed_by,
        )
        await db.commit()

        state_updates = {
            "resolution_outcome": normalized,
            "requires_human_review": False,
            "review_reason": None,
        }
        with contextlib.suppress(Exception):
            await self.resume_service.resume_workflow(
                db=db,
                dispute_id=dispute_id,
                state_updates=state_updates,
                as_node="waiting_resolution_node",
            )

        return {
            "status": "SUCCESS",
            "message": "Decision processed and workflow resumed.",
        }
