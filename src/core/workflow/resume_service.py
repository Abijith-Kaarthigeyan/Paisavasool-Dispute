"""Workflow resume service for resuming paused/interrupted dispute processes."""

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions.business_exceptions import ValidationException
from src.core.workflow.graph import get_graph
from src.data.repositories.dispute_repository import DisputeRepository


class DisputeResumeService:
    """Service to load workflow context and resume graph execution."""

    def __init__(self) -> None:
        pass

    async def resume_workflow(
        self,
        db: AsyncSession,
        dispute_id: UUID,
        state_updates: dict[str, Any] | None = None,
        as_node: str | None = None,
    ) -> dict[str, Any]:
        """Loads context, applies state updates, and triggers LangGraph resumption.

        Args:
            db: Database session.
            dispute_id: Dispute UUID.
            state_updates: Optional variables to merge into state.
            as_node: Node execution context for updates.

        Returns:
            The final state dict of the execution.

        Raises:
            ValidationException: If the dispute is already closed.
        """
        dispute_repo = DisputeRepository(db)
        dispute = await dispute_repo.get_by_id(dispute_id)
        if dispute and dispute.status == "CLOSED":
            raise ValidationException(
                f"Cannot resume workflow for dispute {dispute_id} in status CLOSED."
            )

        config = {
            "configurable": {
                "thread_id": str(dispute_id),
                "db": db,
            }
        }
        graph = get_graph()

        # Update state first if updates are provided
        if state_updates:
            await graph.aupdate_state(config, state_updates, as_node=as_node)

        # Resume the graph execution. Passing None input resumes from the last checkpoint
        res = await graph.ainvoke(None, config)
        return dict(res)
