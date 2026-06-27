from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.models.postgres.internal_team_contact import InternalTeamContact


class InternalTeamContactRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_all(self) -> list[InternalTeamContact]:
        result = await self.db.execute(
            select(InternalTeamContact).order_by(InternalTeamContact.team_key)
        )
        return list(result.scalars().all())

    async def get_by_team_key(self, team_key: str) -> InternalTeamContact | None:
        result = await self.db.execute(
            select(InternalTeamContact).where(InternalTeamContact.team_key == team_key)
        )
        return result.scalar_one_or_none()

    async def update_email(
        self,
        team_key: str,
        *,
        email: str,
        updated_by: UUID | None = None,
    ) -> InternalTeamContact | None:
        contact = await self.get_by_team_key(team_key)
        if not contact:
            return None
        contact.email = email
        contact.updated_by = updated_by
        if not self.db.sync_session._flushing:
            await self.db.flush()
        await self.db.refresh(contact)
        return contact
