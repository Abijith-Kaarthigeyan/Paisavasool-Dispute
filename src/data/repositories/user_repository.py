from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.data.models.postgres.user_mapping import RoleMapping, UserMapping


class UserRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_active_finance_associates(self) -> list[UserMapping]:
        """Fetch all active users with the FINANCE_ASSOCIATE role."""
        result = await self.db.execute(
            select(UserMapping)
            .join(RoleMapping, UserMapping.role_id == RoleMapping.id)
            .where(
                UserMapping.is_active.is_(True),
                RoleMapping.role_name == "FINANCE_ASSOCIATE",
            )
            .options(joinedload(UserMapping.role))
        )
        return list(result.scalars().all())

    async def get_user_by_id(self, user_id: UUID) -> UserMapping | None:
        """Fetch any user by ID."""
        result = await self.db.execute(
            select(UserMapping)
            .where(UserMapping.id == user_id)
            .options(joinedload(UserMapping.role), joinedload(UserMapping.manager))
        )
        return result.scalar_one_or_none()

    async def get_manager_for_associate(self, associate_id: UUID) -> UserMapping | None:
        """Find the manager of a given associate user."""
        associate = await self.get_user_by_id(associate_id)
        if associate and associate.manager_id:
            return await self.get_user_by_id(associate.manager_id)
        return None
