"""Dispatches persisted dispute communications via ar-service Gmail send."""

from typing import Any
from uuid import UUID

from src.core.exceptions.business_exceptions import ARServiceClientException
from src.core.services.audit_service import AuditService
from src.data.clients.ar_service_client import ARServiceClient
from src.data.repositories.case_repository import CaseRepository
from src.data.repositories.communication_repository import CommunicationRepository
from src.observability.logging.logger import logger


def _format_message_id(message_id: str) -> str:
    normalized = message_id.strip()
    if normalized.startswith("<") and normalized.endswith(">"):
        return normalized
    return f"<{normalized.strip('<>')}>"


class OutboundEmailService:
    """Sends persisted dispute communications through ar-service and stores thread IDs."""

    def __init__(
        self,
        *,
        ar_client: ARServiceClient,
        comm_repo: CommunicationRepository,
        case_repo: CaseRepository,
        audit_service: AuditService,
    ):
        self.ar_client = ar_client
        self.comm_repo = comm_repo
        self.case_repo = case_repo
        self.audit_service = audit_service

    async def send_communication(
        self,
        *,
        dispute_id: UUID,
        communication: Any,
        case: Any | None = None,
        use_thread: bool = True,
    ) -> None:
        """Deliver a persisted communication and update thread metadata on success."""
        thread_meta = await self._resolve_thread_metadata(
            dispute_id=dispute_id,
            case=case,
            use_thread=use_thread,
        )
        payload = {
            "to": communication.recipient,
            "subject": communication.subject,
            "body": communication.body,
            **thread_meta,
        }

        try:
            result = await self.ar_client.send_email(payload)
        except ARServiceClientException as exc:
            logger.error(
                "Outbound email failed dispute=%s comm=%s: %s",
                dispute_id,
                communication.id,
                exc,
            )
            await self.audit_service.log_event(
                dispute_id=dispute_id,
                action="OUTBOUND_EMAIL_FAILED",
                metadata={
                    "communication_id": str(communication.id),
                    "recipient": communication.recipient,
                    "error": str(exc),
                },
            )
            return

        if not result.get("sent"):
            logger.info(
                "Outbound email skipped (send disabled) dispute=%s comm=%s",
                dispute_id,
                communication.id,
            )
            return

        await self.comm_repo.update_thread_metadata(
            communication,
            gmail_message_id=result.get("gmail_message_id"),
            rfc_message_id=result.get("rfc_message_id"),
        )

        if case is not None:
            updated = False
            if result.get("gmail_thread_id") and not case.gmail_thread_id:
                case.gmail_thread_id = result["gmail_thread_id"]
                updated = True
            if result.get("rfc_message_id") and not case.rfc_message_id:
                case.rfc_message_id = result["rfc_message_id"]
                updated = True
            if updated:
                await self.case_repo.update_case(case)

    async def _resolve_thread_metadata(
        self,
        *,
        dispute_id: UUID,
        case: Any | None,
        use_thread: bool,
    ) -> dict[str, str | None]:
        if not use_thread:
            return {"thread_id": None, "in_reply_to": None, "references": None}

        thread_id = case.gmail_thread_id if case else None
        in_reply_to = case.rfc_message_id if case else None

        references_tokens: list[str] = []
        if case and case.rfc_message_id:
            references_tokens.append(_format_message_id(case.rfc_message_id))

        comms = await self.comm_repo.list_customer_communications_chronological(
            dispute_id
        )
        for comm in comms:
            if not comm.rfc_message_id:
                continue
            formatted = _format_message_id(comm.rfc_message_id)
            if formatted not in references_tokens:
                references_tokens.append(formatted)

        references = " ".join(references_tokens) if references_tokens else None
        return {
            "thread_id": thread_id,
            "in_reply_to": in_reply_to,
            "references": references,
        }
