"""Workflow interrupt service for recording pauses and reason context."""

from typing import Any
from uuid import UUID

from src.core.services.audit_service import AuditService
from src.data.repositories.workflow_context_repository import WorkflowContextRepository


class WorkflowInterruptService:
    """Manages recording human interrupts and workflow pause states."""

    def __init__(
        self,
        context_repo: WorkflowContextRepository,
        audit_service: AuditService,
    ) -> None:
        self.context_repo = context_repo
        self.audit_service = audit_service

    async def record_interrupt(
        self,
        dispute_id: UUID,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Saves an interrupt trace inside the dispute workflow context."""
        context = await self.context_repo.get_by_dispute_id(dispute_id)
        if not context:
            return

        state = context.workflow_state or {}
        # Keep track of active channels
        state["requires_human_review"] = True
        state["review_reason"] = reason
        state["interrupt_reason"] = reason
        state["interrupt_details"] = details or {}

        # Save back to context
        context.workflow_state = state
        await self.context_repo.update_workflow_context(context)

        # Log audit history trail
        await self.audit_service.log_event(
            dispute_id=dispute_id,
            action="STATUS_CHANGED",
            metadata={
                "field": "workflow_interrupt",
                "reason": reason,
                "details": details or {},
            },
        )
