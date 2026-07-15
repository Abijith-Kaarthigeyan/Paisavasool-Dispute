from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.models.postgres.workflow_context import DisputeWorkflowContext


class WorkflowContextRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, context_id: UUID) -> DisputeWorkflowContext | None:
        result = await self.db.execute(
            select(DisputeWorkflowContext).where(
                DisputeWorkflowContext.id == context_id,
                DisputeWorkflowContext.is_deleted.is_(False),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_dispute_id(
        self, dispute_id: UUID
    ) -> DisputeWorkflowContext | None:
        result = await self.db.execute(
            select(DisputeWorkflowContext).where(
                DisputeWorkflowContext.dispute_id == dispute_id,
                DisputeWorkflowContext.is_deleted.is_(False),
            )
        )
        return result.scalar_one_or_none()

    async def create_workflow_context(
        self,
        *,
        dispute_id: UUID,
        workflow_name: str,
        current_node: str,
        workflow_state: dict,
        last_checkpoint: str | None = None,
    ) -> DisputeWorkflowContext:
        context = DisputeWorkflowContext(
            dispute_id=dispute_id,
            workflow_name=workflow_name,
            current_node=current_node,
            workflow_state=workflow_state,
            last_checkpoint=last_checkpoint,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        self.db.add(context)
        if not self.db.sync_session._flushing:
            await self.db.flush()
        return context

    async def update_workflow_context(
        self, context: DisputeWorkflowContext
    ) -> DisputeWorkflowContext:
        context.updated_at = datetime.now()
        if not self.db.sync_session._flushing:
            await self.db.flush()
        return context
