from typing import Any
from uuid import UUID

from src.core.exceptions.business_exceptions import DisputeNotFoundException
from src.core.services.workflow_context_service import WorkflowContextService
from src.core.workflow.interrupt_service import WorkflowInterruptService
from src.core.workflow.resume_service import DisputeResumeService
from src.data.repositories.dispute_repository import DisputeRepository


class DisputeWorkflowService:
    def __init__(
        self,
        dispute_repo: DisputeRepository,
        workflow_context_service: WorkflowContextService,
        interrupt_service: WorkflowInterruptService,
        resume_service: DisputeResumeService,
    ):
        self.dispute_repo = dispute_repo
        self.workflow_context_service = workflow_context_service
        self.interrupt_service = interrupt_service
        self.resume_service = resume_service

    async def interrupt_workflow(
        self, dispute_id: UUID, performed_by: UUID
    ) -> dict[str, str]:
        db = self.dispute_repo.db
        dispute = await self.dispute_repo.get_by_id(dispute_id)
        if not dispute:
            raise DisputeNotFoundException(f"Dispute {dispute_id} not found.")

        dispute.status = "IN_REVIEW"
        await self.dispute_repo.update_dispute(dispute)
        await self.interrupt_service.record_interrupt(
            dispute_id=dispute_id,
            reason="MANUAL_INTERRUPT",
            details={"interrupted_by": str(performed_by)},
        )
        await db.commit()
        return {"status": "SUCCESS", "message": "Workflow manually interrupted."}

    async def resume_workflow(self, dispute_id: UUID) -> dict[str, Any]:
        db = self.dispute_repo.db
        context = await self.workflow_context_service.get_context(dispute_id)
        state_updates = {
            "requires_human_review": False,
            "review_reason": None,
        }
        context_payload = {
            "id": str(context.id),
            "dispute_id": str(context.dispute_id),
            "workflow_name": context.workflow_name,
            "current_node": context.current_node,
        }

        try:
            final_state = await self.resume_service.resume_workflow(
                db=db,
                dispute_id=dispute_id,
                state_updates=state_updates,
            )
            return {
                "status": "SUCCESS",
                "message": "Workflow resumed.",
                "final_state": final_state,
                "context": context_payload,
            }
        except Exception as e:
            return {
                "status": "SUCCESS",
                "message": f"Workflow resumed and paused: {str(e)}",
                "context": context_payload,
            }
