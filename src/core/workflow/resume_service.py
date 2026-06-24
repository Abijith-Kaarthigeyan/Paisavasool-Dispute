"""Workflow resume service for resuming paused/interrupted dispute processes."""

from typing import Any, Dict, Optional
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession


class DisputeResumeService:
    """Service to load workflow context and resume graph execution."""

    def __init__(self) -> None:
        pass

    async def resume_workflow(
        self,
        db: AsyncSession,
        dispute_id: UUID,
        state_updates: Optional[Dict[str, Any]] = None,
        as_node: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Loads context, applies state updates, and triggers LangGraph resumption.

        Args:
            db: Database session.
            dispute_id: Dispute UUID.
            state_updates: Optional variables to merge into state.
            as_node: Node execution context for updates.

        Returns:
            The final state dict of the execution.
        """
        # Import dynamically to avoid circular import issues
        from src.core.workflow.graph import get_graph

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
