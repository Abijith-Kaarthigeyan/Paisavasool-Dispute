from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.models.postgres.communication_draft import DisputeCommunicationDraft


class CommunicationDraftRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_generating(
        self,
        *,
        dispute_id: UUID,
        trigger: str,
        source_communication_id: UUID | None = None,
    ) -> DisputeCommunicationDraft:
        draft = DisputeCommunicationDraft(
            dispute_id=dispute_id,
            trigger=trigger,
            source_communication_id=source_communication_id,
            status="GENERATING",
        )
        self.db.add(draft)
        if not self.db.sync_session._flushing:
            await self.db.flush()
        return draft

    async def mark_ready(
        self,
        draft: DisputeCommunicationDraft,
        *,
        recipient: str,
        subject: str,
        body: str,
    ) -> DisputeCommunicationDraft:
        draft.recipient = recipient
        draft.subject = subject
        draft.body = body
        draft.status = "READY"
        if not self.db.sync_session._flushing:
            await self.db.flush()
        return draft

    async def mark_sent_for_dispute(self, dispute_id: UUID) -> None:
        await self.db.execute(
            update(DisputeCommunicationDraft)
            .where(
                DisputeCommunicationDraft.dispute_id == dispute_id,
                DisputeCommunicationDraft.status == "READY",
            )
            .values(status="SENT")
        )
        if not self.db.sync_session._flushing:
            await self.db.flush()

    async def get_latest_ready(
        self, dispute_id: UUID
    ) -> DisputeCommunicationDraft | None:
        result = await self.db.execute(
            select(DisputeCommunicationDraft)
            .where(
                DisputeCommunicationDraft.dispute_id == dispute_id,
                DisputeCommunicationDraft.status == "READY",
            )
            .order_by(DisputeCommunicationDraft.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_latest_active(
        self, dispute_id: UUID
    ) -> DisputeCommunicationDraft | None:
        """Latest draft that is still actionable (GENERATING or READY, not SENT)."""
        result = await self.db.execute(
            select(DisputeCommunicationDraft)
            .where(
                DisputeCommunicationDraft.dispute_id == dispute_id,
                DisputeCommunicationDraft.status.in_(("GENERATING", "READY")),
            )
            .order_by(DisputeCommunicationDraft.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
