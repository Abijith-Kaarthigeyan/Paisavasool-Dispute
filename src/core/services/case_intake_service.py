from datetime import datetime
from uuid import UUID

from src.core.services.case_attachment_service import CaseAttachmentService
from src.data.repositories.case_repository import CaseRepository
from src.data.repositories.dispute_repository import DisputeRepository
from src.data.repositories.other_repositories import ActivityRepository
from src.infrastructure.celery.tasks import process_dispute_case
from src.observability.logging.logger import logger
from src.schemas.case import CaseIntakeRequest


class CaseIntakeService:
    def __init__(
        self,
        case_repo: CaseRepository,
        dispute_repo: DisputeRepository,
        activity_repo: ActivityRepository,
        attachment_service: CaseAttachmentService,
    ):
        self.case_repo = case_repo
        self.dispute_repo = dispute_repo
        self.activity_repo = activity_repo
        self.attachment_service = attachment_service

    async def _generate_case_number(self) -> str:
        year = datetime.now().year
        count = await self.case_repo.count_cases()
        return f"CASE-{year}-{count + 1:06d}"

    async def process_intake(
        self,
        payload: CaseIntakeRequest,
        performed_by: UUID,
    ) -> dict:
        """Processes customer email intake with idempotency and queues triage workflow."""
        db = self.case_repo.db
        message_id = payload.message_id

        if message_id:
            existing = await self.case_repo.find_by_original_message_id(message_id)
            if existing:
                logger.info(
                    "Idempotency triggered for intake message_id: %s. Reusing Case %s",
                    message_id,
                    existing.id,
                )
                disputes = await self.dispute_repo.list_disputes_by_case_id(existing.id)
                for dispute in disputes:
                    await self.activity_repo.create_activity(
                        dispute_id=dispute.id,
                        activity_type="INTAKE_IDEMPOTENCY",
                        metadata={
                            "info": "Intake email with message_id was resubmitted and bypassed"
                        },
                        performed_by=performed_by,
                    )
                await db.commit()
                return {
                    "case_id": existing.id,
                    "case_number": existing.case_number,
                    "status": "DUPLICATE_BYPASS",
                }

        case = await self.case_repo.create_case(
            case_number=await self._generate_case_number(),
            customer_email=payload.customer_email,
            email_subject=payload.email_subject,
            email_body=payload.email_body,
            original_message_id=message_id,
            gmail_thread_id=payload.gmail_thread_id,
            rfc_message_id=payload.rfc_message_id,
            raw_content=payload.raw_content,
        )
        if payload.attachments:
            await self.attachment_service.persist_attachments(
                case.id, payload.attachments
            )
        await db.commit()

        process_dispute_case.delay(
            str(case.id),
            in_reply_to=payload.in_reply_to or "",
            email_references=payload.references or "",
        )

        return {
            "case_id": case.id,
            "case_number": case.case_number,
            "status": "QUEUED",
        }
