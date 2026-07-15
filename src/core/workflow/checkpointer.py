"""LangGraph checkpoint saver using Postgres dispute_workflow_context."""

import json
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from sqlalchemy.orm.attributes import flag_modified

from src.data.repositories.dispute_repository import DisputeRepository
from src.data.repositories.workflow_context_repository import WorkflowContextRepository


class DbWorkflowCheckpointer(BaseCheckpointSaver):
    """Saves and loads LangGraph checkpoints using the dispute_workflow_context table."""

    def __init__(self) -> None:
        super().__init__()

    async def _is_dispute(self, db: Any, thread_id: str) -> bool:
        try:
            dispute_id = UUID(thread_id)
        except ValueError:
            return False

        dispute_repo = DisputeRepository(db)
        return await dispute_repo.exists(dispute_id)

    def _dumps(self, obj: Any) -> str:
        if obj is None:
            return "null:"
        t, b = self.serde.dumps_typed(obj)
        return f"{t}:{b.hex()}"

    def _loads(self, s: str) -> Any:
        if not s:
            return None
        if s == "null:":
            return None
        if ":" in s:
            parts = s.split(":", 1)
            t, h = parts[0], parts[1]
            try:
                b = bytes.fromhex(h)
                return self.serde.loads_typed((t, b))
            except Exception:
                pass
        # Fallback for json loads
        try:
            return json.loads(s)
        except Exception:
            return s

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        """Synchronous checkpoint retrieval (not supported in this async-first app)."""
        raise NotImplementedError("Use async aget_tuple method instead.")

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        """Synchronous checkpoint listing (not supported)."""
        raise NotImplementedError("Use async alist method instead.")

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        """Synchronous checkpoint storage (not supported)."""
        raise NotImplementedError("Use async aput method instead.")

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """Synchronous intermediate writes storage (not supported)."""
        raise NotImplementedError("Use async aput_writes method instead.")

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        """Asynchronously retrieve a checkpoint tuple using configuration."""
        db = config.get("configurable", {}).get("db")
        thread_id = config.get("configurable", {}).get("thread_id")
        if not db or not thread_id:
            return None

        if not await self._is_dispute(db, thread_id):
            return None

        dispute_id = UUID(thread_id)
        context_repo = WorkflowContextRepository(db)
        context = await context_repo.get_by_dispute_id(dispute_id)
        if not context or not context.workflow_state:
            return None

        state_dict = context.workflow_state
        if "checkpoints" not in state_dict:
            return None

        checkpoint_id = config.get("configurable", {}).get("checkpoint_id")
        if not checkpoint_id:
            checkpoint_id = state_dict.get("latest_checkpoint_id")

        if not checkpoint_id or checkpoint_id not in state_dict["checkpoints"]:
            return None

        checkpoint_data = state_dict["checkpoints"][checkpoint_id]

        checkpoint = self._loads(checkpoint_data["checkpoint"])
        metadata = self._loads(checkpoint_data["metadata"])
        parent_config = checkpoint_data.get("parent_config")

        pending_writes = None
        pw_str = checkpoint_data.get("pending_writes")
        if pw_str:
            pending_writes = self._loads(pw_str)

        return CheckpointTuple(
            config=config,
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=parent_config,
            pending_writes=pending_writes,
        )

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        """Asynchronously store a checkpoint."""
        db = config.get("configurable", {}).get("db")
        thread_id = config.get("configurable", {}).get("thread_id")
        if not db or not thread_id:
            return config

        if not await self._is_dispute(db, thread_id):
            return config

        dispute_id = UUID(thread_id)
        context_repo = WorkflowContextRepository(db)
        context = await context_repo.get_by_dispute_id(dispute_id)

        checkpoint_id = checkpoint["id"]
        checkpoint_str = self._dumps(checkpoint)
        metadata_str = self._dumps(metadata)

        parent_config = None
        if parent_checkpoint_id := checkpoint.get("parent_checkpoint_id"):
            parent_config = {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_id": parent_checkpoint_id,
                }
            }

        checkpoint_entry = {
            "checkpoint": checkpoint_str,
            "metadata": metadata_str,
            "parent_config": parent_config,
            "pending_writes": None,
        }

        if context:
            state_dict = context.workflow_state or {}
            if "checkpoints" not in state_dict:
                state_dict["checkpoints"] = {}
            state_dict["checkpoints"][checkpoint_id] = checkpoint_entry
            state_dict["latest_checkpoint_id"] = checkpoint_id

            context.workflow_state = state_dict
            context.last_checkpoint = checkpoint_id

            # Update current node if available in metadata
            node_name = (
                metadata.get("source_node")
                or metadata.get("node")
                or context.current_node
            )
            if node_name:
                context.current_node = node_name

            flag_modified(context, "workflow_state")
            await context_repo.update_workflow_context(context)
        else:
            state_dict = {
                "checkpoints": {checkpoint_id: checkpoint_entry},
                "latest_checkpoint_id": checkpoint_id,
            }
            node_name = metadata.get("source_node") or metadata.get("node") or "START"
            await context_repo.create_workflow_context(
                dispute_id=dispute_id,
                workflow_name="dispute_workflow",
                current_node=node_name,
                workflow_state=state_dict,
                last_checkpoint=checkpoint_id,
            )

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_id": checkpoint_id,
            }
        }

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """Asynchronously store intermediate writes."""
        db = config.get("configurable", {}).get("db")
        thread_id = config.get("configurable", {}).get("thread_id")
        checkpoint_id = config.get("configurable", {}).get("checkpoint_id")
        if not db or not thread_id or not checkpoint_id:
            return

        if not await self._is_dispute(db, thread_id):
            return

        dispute_id = UUID(thread_id)
        context_repo = WorkflowContextRepository(db)
        context = await context_repo.get_by_dispute_id(dispute_id)
        if not context or not context.workflow_state:
            return

        state_dict = context.workflow_state
        if "checkpoints" not in state_dict:
            return

        if checkpoint_id in state_dict["checkpoints"]:
            serialized_writes = self._dumps(writes)
            state_dict["checkpoints"][checkpoint_id]["pending_writes"] = (
                serialized_writes
            )
            context.workflow_state = state_dict
            flag_modified(context, "workflow_state")
            await context_repo.update_workflow_context(context)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        """Asynchronously list checkpoints matching criteria."""
        if not config:
            return
        db = config.get("configurable", {}).get("db")
        thread_id = config.get("configurable", {}).get("thread_id")
        if not db or not thread_id:
            return

        if not await self._is_dispute(db, thread_id):
            return

        dispute_id = UUID(thread_id)
        context_repo = WorkflowContextRepository(db)
        context = await context_repo.get_by_dispute_id(dispute_id)
        if not context or not context.workflow_state:
            return

        state_dict = context.workflow_state
        if "checkpoints" not in state_dict:
            return

        checkpoints = state_dict["checkpoints"]
        for cp_id, cp_data in checkpoints.items():
            if before and before.get("configurable", {}).get("checkpoint_id") == cp_id:
                continue

            checkpoint = self._loads(cp_data["checkpoint"])
            metadata = self._loads(cp_data["metadata"])
            parent_config = cp_data.get("parent_config")

            pending_writes = None
            pw_str = cp_data.get("pending_writes")
            if pw_str:
                pending_writes = self._loads(pw_str)

            yield CheckpointTuple(
                config={
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_id": cp_id,
                    }
                },
                checkpoint=checkpoint,
                metadata=metadata,
                parent_config=parent_config,
                pending_writes=pending_writes,
            )
