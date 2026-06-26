from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.services.email_thread_utils import normalize_message_token
from src.data.models.postgres.communication import DisputeCommunication


class CommunicationRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, comm_id: UUID) -> DisputeCommunication | None:
        result = await self.db.execute(
            select(DisputeCommunication).where(
                DisputeCommunication.id == comm_id,
                DisputeCommunication.is_deleted.is_(False),
            )
        )
        return result.scalar_one_or_none()

    async def get_communications_for_dispute(
        self, dispute_id: UUID
    ) -> list[DisputeCommunication]:
        result = await self.db.execute(
            select(DisputeCommunication)
            .where(
                DisputeCommunication.dispute_id == dispute_id,
                DisputeCommunication.is_deleted.is_(False),
            )
            .order_by(DisputeCommunication.created_at.desc())
        )
        return list(result.scalars().all())

    async def list_customer_communications_chronological(
        self, dispute_id: UUID
    ) -> list[DisputeCommunication]:
        result = await self.db.execute(
            select(DisputeCommunication)
            .where(
                DisputeCommunication.dispute_id == dispute_id,
                DisputeCommunication.communication_type == "CUSTOMER",
                DisputeCommunication.is_deleted.is_(False),
            )
            .order_by(DisputeCommunication.created_at.asc())
        )
        return list(result.scalars().all())

    async def find_by_message_token(self, token: str) -> list[DisputeCommunication]:
        normalized = normalize_message_token(token)
        if not normalized:
            return []

        result = await self.db.execute(
            select(DisputeCommunication).where(
                DisputeCommunication.is_deleted.is_(False),
            )
        )
        matches: list[DisputeCommunication] = []
        for communication in result.scalars().all():
            if (
                normalize_message_token(communication.rfc_message_id) == normalized
                or normalize_message_token(communication.gmail_message_id) == normalized
            ):
                matches.append(communication)
        return matches

    async def create_communication(
        self,
        *,
        dispute_id: UUID,
        recipient: str,
        subject: str,
        body: str,
        communication_type: str,
    ) -> DisputeCommunication:
        comm = DisputeCommunication(
            dispute_id=dispute_id,
            recipient=recipient,
            subject=subject,
            body=body,
            communication_type=communication_type,
            created_at=datetime.now(),
        )
        self.db.add(comm)
        if not self.db.sync_session._flushing:
            await self.db.flush()
        return comm
