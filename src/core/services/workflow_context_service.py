from uuid import UUID

from src.core.exceptions.business_exceptions import (
    WorkflowException,
)
from src.core.services.audit_service import AuditService
from src.data.models.postgres.workflow_context import DisputeWorkflowContext
from src.data.repositories.workflow_context_repository import WorkflowContextRepository


class WorkflowContextService:
    def __init__(
        self,
        context_repo: WorkflowContextRepository,
        audit_service: AuditService,
    ):
        self.context_repo = context_repo
        self.audit_service = audit_service

    async def save_checkpoint(
        self,
        *,
        dispute_id: UUID,
        workflow_name: str,
        current_node: str,
        workflow_state: dict,
        last_checkpoint: str | None = None,
    ) -> DisputeWorkflowContext:
        """Saves or updates the checkpoint of a dispute workflow context."""
        context = await self.context_repo.get_by_dispute_id(dispute_id)

        if context:
            # Update existing
            context.workflow_name = workflow_name
            context.current_node = current_node
            context.workflow_state = workflow_state
            context.last_checkpoint = last_checkpoint
            context = await self.context_repo.update_workflow_context(context)
        else:
            # Create new
            context = await self.context_repo.create_workflow_context(
                dispute_id=dispute_id,
                workflow_name=workflow_name,
                current_node=current_node,
                workflow_state=workflow_state,
                last_checkpoint=last_checkpoint,
            )

        await self.audit_service.log_event(
            dispute_id=dispute_id,
            action="STATUS_CHANGED",  # standard activity log mapping
            metadata={
                "field": "workflow_checkpoint",
                "workflow_name": workflow_name,
                "current_node": current_node,
                "last_checkpoint": last_checkpoint,
            },
        )

        return context

    async def load_checkpoint(self, dispute_id: UUID) -> DisputeWorkflowContext:
        """Loads the current checkpoint context for a dispute."""
        context = await self.context_repo.get_by_dispute_id(dispute_id)
        if not context:
            raise WorkflowException(
                f"No workflow context found for dispute {dispute_id}."
            )
        return context

    async def update_node(
        self, dispute_id: UUID, current_node: str, workflow_state: dict
    ) -> DisputeWorkflowContext:
        """Updates the workflow state and node name in context."""
        context = await self.load_checkpoint(dispute_id)
        context.current_node = current_node
        context.workflow_state = workflow_state
        return await self.context_repo.update_workflow_context(context)

    async def resume_context(self, dispute_id: UUID) -> dict:
        """Resumes the workflow context, returning the state payload."""
        context = await self.load_checkpoint(dispute_id)
        return {
            "dispute_id": str(dispute_id),
            "workflow_name": context.workflow_name,
            "current_node": context.current_node,
            "workflow_state": context.workflow_state,
            "last_checkpoint": context.last_checkpoint,
        }


DefinitionName = "WorkflowContextService"
