"""Associate-initiated customer communications with AI-assisted drafting."""

import json
from typing import Any
from uuid import UUID

from src.core.exceptions.business_exceptions import ValidationException
from src.core.services.audit_service import AuditService
from src.core.services.conversation_history_service import ConversationHistoryService
from src.core.services.outbound_email_service import OutboundEmailService
from src.core.workflow.mail_agent import DisputeMailAgent
from src.data.clients.ar_service_client import ARServiceClient
from src.data.models.postgres.communication_draft import DisputeCommunicationDraft
from src.data.repositories.communication_draft_repository import (
    CommunicationDraftRepository,
)
from src.data.repositories.communication_repository import CommunicationRepository
from src.data.repositories.other_repositories import (
    ActivityRepository,
    CommentRepository,
)
from src.data.repositories.workflow_context_repository import WorkflowContextRepository


def _is_system_outbound_communication(comm: Any) -> bool:
    if comm.communication_type == "ASSOCIATE_OUTBOUND":
        return True
    if comm.communication_type != "CUSTOMER":
        return False
    subject = (comm.subject or "").lower()
    body = (comm.body or "").lower()
    markers = (
        "paisa vasool",
        "dear customer",
        "regarding your dispute",
        "update on your dispute",
    )
    return any(marker in subject or marker in body for marker in markers)


class AssociateCommunicationService:
    """Drafts and persists associate-authored outbound customer emails."""

    def __init__(
        self,
        *,
        comm_repo: CommunicationRepository,
        draft_repo: CommunicationDraftRepository,
        activity_repo: ActivityRepository,
        comment_repo: CommentRepository,
        context_repo: WorkflowContextRepository,
        audit_service: AuditService,
        conversation_history_service: ConversationHistoryService,
        ar_client: ARServiceClient,
        outbound_email_service: OutboundEmailService,
    ):
        self.comm_repo = comm_repo
        self.draft_repo = draft_repo
        self.activity_repo = activity_repo
        self.comment_repo = comment_repo
        self.context_repo = context_repo
        self.audit_service = audit_service
        self.conversation_history_service = conversation_history_service
        self.ar_client = ar_client
        self.outbound_email_service = outbound_email_service

    async def _resolve_customer_email(self, dispute: Any) -> str:
        case = getattr(dispute, "case", None)
        if case and case.customer_email:
            return case.customer_email
        return "customer@example.com"

    async def _resolve_invoice_summary(self, dispute: Any) -> str:
        context = await self.context_repo.get_by_dispute_id(dispute.id)
        if context and context.workflow_state:
            invoice_json = (context.workflow_state.get("metadata") or {}).get(
                "invoice_json"
            )
            if invoice_json:
                return json.dumps(invoice_json)

        try:
            inv_details = await self.ar_client.get_invoice_details(dispute.invoice_id)
            formatted = {
                "invoice_number": inv_details.get(
                    "invoice_number", dispute.invoice_number
                ),
                "invoice_date": inv_details.get("invoice_date", ""),
                "due_date": inv_details.get("due_date", ""),
                "subtotal_amount": float(inv_details.get("subtotal_amount", 0)),
                "tax_amount": float(inv_details.get("tax_amount", 0)),
                "total_amount": float(inv_details.get("total_amount", 0)),
                "outstanding_amount": float(inv_details.get("outstanding_amount", 0)),
                "status": inv_details.get("status", ""),
            }
            return json.dumps(formatted)
        except Exception:
            return json.dumps({"invoice_number": dispute.invoice_number})

    async def _resolve_payment_reference(self, dispute: Any) -> str | None:
        context = await self.context_repo.get_by_dispute_id(dispute.id)
        if context and context.workflow_state:
            ref = (context.workflow_state.get("metadata") or {}).get(
                "extracted_reference_number"
            )
            if ref:
                return str(ref)
        return None

    async def _build_prior_outbound_summary(self, dispute_id: UUID) -> str:
        comms = await self.comm_repo.get_communications_for_dispute(dispute_id)
        lines: list[str] = []
        for comm in reversed(comms):
            if not _is_system_outbound_communication(comm):
                continue
            preview = (comm.body or "").strip().replace("\n", " ")[:240]
            source = (
                "Associate"
                if comm.communication_type == "ASSOCIATE_OUTBOUND"
                else "System"
            )
            lines.append(f"- [{source}] {comm.subject}: {preview}")
        return "\n".join(lines) if lines else "None"

    async def gather_context(self, dispute: Any) -> dict[str, Any]:
        """Collects dispute context used by the mail generation agent."""
        dispute_id = dispute.id
        activities = await self.activity_repo.list_activities_for_dispute(dispute_id)
        comments = await self.comment_repo.list_comments_for_dispute(dispute_id)

        act_summary = "\n".join(
            f"- {a.activity_type}: {a.activity_metadata}" for a in activities
        )
        comm_summary = "\n".join(f"- {c.comment_type}: {c.comment}" for c in comments)

        resolution_reason = None
        for activity in reversed(activities):
            if (
                activity.activity_type == "PAYMENT_OUTCOME_PROPOSED"
                and activity.activity_metadata
            ):
                resolution_reason = activity.activity_metadata.get("reason")
                break

        customer_message = (
            await self.conversation_history_service.build_customer_conversation_text(
                dispute_id,
                dispute,
            )
        )

        return {
            "dispute_category": dispute.dispute_category,
            "dispute_status": dispute.status,
            "resolution_outcome": dispute.resolution_outcome,
            "customer_email": await self._resolve_customer_email(dispute),
            "invoice_number": dispute.invoice_number,
            "activities_summary": act_summary,
            "comments_summary": comm_summary,
            "invoice_summary": await self._resolve_invoice_summary(dispute),
            "customer_message_summary": customer_message,
            "payment_reference": await self._resolve_payment_reference(dispute),
            "resolution_reason": resolution_reason,
            "prior_outbound_summary": await self._build_prior_outbound_summary(
                dispute_id
            ),
        }

    async def draft_email(
        self,
        dispute: Any,
        *,
        associate_instructions: str | None = None,
    ) -> dict[str, Any]:
        """Generates an editable email draft for associate review."""
        context = await self.gather_context(dispute)
        return await DisputeMailAgent.generate_associate_draft(
            **context,
            associate_instructions=associate_instructions,
        )

    async def generate_and_persist_draft(
        self,
        dispute: Any,
        *,
        trigger: str,
        source_communication_id: UUID | None = None,
        instructions: str | None = None,
    ) -> DisputeCommunicationDraft:
        """Creates a GENERATING draft row, runs LLM drafting, and marks it READY."""
        draft = await self.draft_repo.create_generating(
            dispute_id=dispute.id,
            trigger=trigger,
            source_communication_id=source_communication_id,
        )
        result = await self.draft_email(
            dispute,
            associate_instructions=instructions,
        )
        return await self.draft_repo.mark_ready(
            draft,
            recipient=result["recipient"],
            subject=result["subject"],
            body=result["body"],
        )

    async def get_latest_draft(
        self, dispute_id: UUID
    ) -> DisputeCommunicationDraft | None:
        return await self.draft_repo.get_latest_active(dispute_id)

    async def mark_drafts_sent(self, dispute_id: UUID) -> None:
        await self.draft_repo.mark_sent_for_dispute(dispute_id)

    async def send_email(
        self,
        dispute: Any,
        *,
        recipient: str,
        subject: str,
        body: str,
        sent_by: UUID,
    ) -> Any:
        """Persists an associate-authored outbound email to the dispute thread."""
        if not subject.strip() or not body.strip():
            raise ValidationException("Subject and body are required.")
        if not recipient.strip():
            raise ValidationException("Recipient is required.")

        comm = await self.comm_repo.create_communication(
            dispute_id=dispute.id,
            recipient=recipient.strip(),
            subject=subject.strip(),
            body=body.strip(),
            communication_type="ASSOCIATE_OUTBOUND",
        )

        await self.audit_service.log_event(
            dispute_id=dispute.id,
            action="ASSOCIATE_EMAIL_SENT",
            metadata={
                "communication_id": str(comm.id),
                "recipient": recipient,
                "subject": subject,
                "sent_by": str(sent_by),
            },
            performed_by=sent_by,
        )

        await self.comment_repo.create_comment(
            dispute_id=dispute.id,
            comment=f"Associate email sent to {recipient}: {subject}",
            comment_type="INTERNAL",
            created_by=sent_by,
        )

        await self.outbound_email_service.send_communication(
            dispute_id=dispute.id,
            communication=comm,
            case=getattr(dispute, "case", None),
            use_thread=True,
        )

        await self.mark_drafts_sent(dispute.id)
        await self.comm_repo.db.commit()
        return comm
