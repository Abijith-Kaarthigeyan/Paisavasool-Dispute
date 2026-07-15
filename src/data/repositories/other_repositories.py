from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.models.postgres.activity import DisputeActivity
from src.data.models.postgres.agent_run import DisputeAgentRun
from src.data.models.postgres.attachment import DisputeAttachment
from src.data.models.postgres.comment import DisputeComment
from src.data.models.postgres.evidence_snapshot import DisputeEvidenceSnapshot


def sanitize_for_json(data: Any) -> Any:
    """Recursively converts non-serializable objects (UUID, Decimal, datetime, date) into strings."""
    if isinstance(data, dict):
        return {k: sanitize_for_json(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [sanitize_for_json(v) for v in data]
    elif isinstance(data, (UUID, Decimal)):
        return str(data)
    elif isinstance(data, (datetime, date)):
        return data.isoformat()
    return data


class CommentRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, comment_id: UUID) -> DisputeComment | None:
        result = await self.db.execute(
            select(DisputeComment).where(
                DisputeComment.id == comment_id,
                DisputeComment.is_deleted.is_(False),
            )
        )
        return result.scalar_one_or_none()

    async def list_comments_for_dispute(self, dispute_id: UUID) -> list[DisputeComment]:
        result = await self.db.execute(
            select(DisputeComment)
            .where(
                DisputeComment.dispute_id == dispute_id,
                DisputeComment.is_deleted.is_(False),
            )
            .order_by(DisputeComment.created_at.asc())
        )
        return list(result.scalars().all())

    async def create_comment(
        self,
        *,
        dispute_id: UUID,
        comment: str,
        comment_type: str,
        created_by: UUID,
    ) -> DisputeComment:
        comm = DisputeComment(
            dispute_id=dispute_id,
            comment=comment,
            comment_type=comment_type,
            created_by=created_by,
            created_at=datetime.now(),
        )
        self.db.add(comm)
        if not self.db.sync_session._flushing:
            await self.db.flush()
        return comm


class AttachmentRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, attachment_id: UUID) -> DisputeAttachment | None:
        result = await self.db.execute(
            select(DisputeAttachment).where(
                DisputeAttachment.id == attachment_id,
                DisputeAttachment.is_deleted.is_(False),
            )
        )
        return result.scalar_one_or_none()

    async def list_attachments_for_dispute(
        self, dispute_id: UUID
    ) -> list[DisputeAttachment]:
        result = await self.db.execute(
            select(DisputeAttachment)
            .where(
                DisputeAttachment.dispute_id == dispute_id,
                DisputeAttachment.is_deleted.is_(False),
            )
            .order_by(DisputeAttachment.uploaded_at.desc())
        )
        return list(result.scalars().all())

    async def create_attachment(
        self,
        *,
        dispute_id: UUID,
        file_name: str,
        file_path: str,
        uploaded_by: UUID,
    ) -> DisputeAttachment:
        attachment = DisputeAttachment(
            dispute_id=dispute_id,
            file_name=file_name,
            file_path=file_path,
            uploaded_by=uploaded_by,
            uploaded_at=datetime.now(),
        )
        self.db.add(attachment)
        if not self.db.sync_session._flushing:
            await self.db.flush()
        return attachment


class ActivityRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, activity_id: UUID) -> DisputeActivity | None:
        result = await self.db.execute(
            select(DisputeActivity).where(
                DisputeActivity.id == activity_id,
                DisputeActivity.is_deleted.is_(False),
            )
        )
        return result.scalar_one_or_none()

    async def list_activities_for_dispute(
        self, dispute_id: UUID
    ) -> list[DisputeActivity]:
        result = await self.db.execute(
            select(DisputeActivity)
            .where(
                DisputeActivity.dispute_id == dispute_id,
                DisputeActivity.is_deleted.is_(False),
            )
            .order_by(DisputeActivity.created_at.desc())
        )
        return list(result.scalars().all())

    async def create_activity(
        self,
        *,
        dispute_id: UUID,
        activity_type: str,
        metadata: dict | None = None,
        performed_by: UUID | None = None,
    ) -> DisputeActivity:
        activity = DisputeActivity(
            dispute_id=dispute_id,
            activity_type=activity_type,
            activity_metadata=sanitize_for_json(metadata),
            performed_by=performed_by,
            created_at=datetime.now(),
        )
        self.db.add(activity)
        if not self.db.sync_session._flushing:
            await self.db.flush()
        return activity


class AgentRunRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, run_id: UUID) -> DisputeAgentRun | None:
        result = await self.db.execute(
            select(DisputeAgentRun).where(
                DisputeAgentRun.id == run_id,
                DisputeAgentRun.is_deleted.is_(False),
            )
        )
        return result.scalar_one_or_none()

    async def create_agent_run(
        self,
        *,
        dispute_id: UUID,
        agent_name: str,
        input_payload: dict | None = None,
        status: str = "RUNNING",
    ) -> DisputeAgentRun:
        run = DisputeAgentRun(
            dispute_id=dispute_id,
            agent_name=agent_name,
            input_payload=sanitize_for_json(input_payload),
            status=status,
            started_at=datetime.now(),
        )
        self.db.add(run)
        if not self.db.sync_session._flushing:
            await self.db.flush()
        return run

    async def update_agent_run(self, run: DisputeAgentRun) -> DisputeAgentRun:
        if run.output_payload:
            run.output_payload = sanitize_for_json(run.output_payload)
        if not self.db.sync_session._flushing:
            await self.db.flush()
            await self.db.refresh(run)
        return run


class EvidenceSnapshotRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, snapshot_id: UUID) -> DisputeEvidenceSnapshot | None:
        result = await self.db.execute(
            select(DisputeEvidenceSnapshot).where(
                DisputeEvidenceSnapshot.id == snapshot_id,
                DisputeEvidenceSnapshot.is_deleted.is_(False),
            )
        )
        return result.scalar_one_or_none()

    async def list_for_dispute(self, dispute_id: UUID) -> list[DisputeEvidenceSnapshot]:
        result = await self.db.execute(
            select(DisputeEvidenceSnapshot).where(
                DisputeEvidenceSnapshot.dispute_id == dispute_id,
                DisputeEvidenceSnapshot.is_deleted.is_(False),
            )
        )
        return list(result.scalars().all())

    async def create_evidence_snapshot(
        self,
        *,
        dispute_id: UUID,
        snapshot_type: str,
        snapshot_data: dict,
    ) -> DisputeEvidenceSnapshot:
        snap = DisputeEvidenceSnapshot(
            dispute_id=dispute_id,
            snapshot_type=snapshot_type,
            snapshot_data=sanitize_for_json(snapshot_data),
            created_at=datetime.now(),
        )
        self.db.add(snap)
        if not self.db.sync_session._flushing:
            await self.db.flush()
        return snap
