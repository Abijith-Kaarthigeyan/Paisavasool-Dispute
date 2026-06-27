"""Builds chronological customer conversation context for dispute agents."""

from typing import Any
from uuid import UUID

from src.data.repositories.communication_repository import CommunicationRepository
from src.data.repositories.other_repositories import CommentRepository


class ConversationHistoryService:
    """Assembles full customer message history for multi-turn dispute resolution."""

    def __init__(
        self,
        comm_repo: CommunicationRepository,
        comment_repo: CommentRepository,
    ):
        self.comm_repo = comm_repo
        self.comment_repo = comment_repo

    async def build_customer_conversation_text(
        self,
        dispute_id: UUID,
        dispute: Any,
        *,
        state_fallback: dict[str, Any] | None = None,
    ) -> str:
        """Returns chronological customer messages for agent prompts."""
        parts: list[str] = []
        seen_bodies: set[str] = set()

        def _add_entry(label: str, subject: str | None, body: str) -> None:
            normalized_body = (body or "").strip()
            if not normalized_body:
                return
            key = normalized_body.lower()
            if key in seen_bodies:
                return
            seen_bodies.add(key)
            subject_line = f"Subject: {subject}\n" if subject else ""
            parts.append(f"--- {label} ---\n{subject_line}{normalized_body}")

        case = getattr(dispute, "case", None)
        if case:
            case_content = (case.raw_content or case.email_body or "").strip()
            if case_content:
                _add_entry("Original customer email", case.email_subject, case_content)

        comms = await self.comm_repo.list_customer_communications_chronological(
            dispute_id
        )
        for index, comm in enumerate(comms, start=1):
            if comm.communication_type != "CUSTOMER":
                continue
            _add_entry(
                f"Customer message #{index}",
                comm.subject,
                comm.body,
            )

        comments = await self.comment_repo.list_comments_for_dispute(dispute_id)
        follow_up_index = 0
        for comment in comments:
            if comment.comment_type != "CUSTOMER_COMMENT":
                continue
            follow_up_index += 1
            _add_entry(
                f"Customer follow-up #{follow_up_index}",
                None,
                comment.comment,
            )

        if not parts and state_fallback:
            fallback = state_fallback.get("raw_content") or (
                f"Subject: {state_fallback.get('email_subject') or ''}\n"
                f"Body: {state_fallback.get('email_body') or ''}"
            )
            if fallback.strip():
                parts.append(fallback.strip())

        return "\n\n".join(parts)
