from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.data.models.postgres.case import DisputeCase


class CaseRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, case_id: UUID) -> DisputeCase | None:
        result = await self.db.execute(
            select(DisputeCase)
            .options(selectinload(DisputeCase.disputes))
            .where(
                DisputeCase.id == case_id,
                DisputeCase.is_deleted.is_(False),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_case_number(self, case_number: str) -> DisputeCase | None:
        result = await self.db.execute(
            select(DisputeCase).where(
                DisputeCase.case_number == case_number,
                DisputeCase.is_deleted.is_(False),
            )
        )
        return result.scalar_one_or_none()

    async def list_cases(self, limit: int = 100, offset: int = 0) -> list[DisputeCase]:
        result = await self.db.execute(
            select(DisputeCase)
            .where(DisputeCase.is_deleted.is_(False))
            .order_by(DisputeCase.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def create_case(
        self,
        *,
        case_number: str,
        customer_email: str,
        email_subject: str | None = None,
        email_body: str | None = None,
        original_message_id: str | None = None,
        raw_content: str | None = None,
        status: str = "OPEN",
    ) -> DisputeCase:
        case = DisputeCase(
            case_number=case_number,
            customer_email=customer_email,
            email_subject=email_subject,
            email_body=email_body,
            original_message_id=original_message_id,
            raw_content=raw_content,
            status=status,
        )
        self.db.add(case)
        if not self.db.sync_session._flushing:
            await self.db.flush()
        return case

    async def update_case(self, case: DisputeCase) -> DisputeCase:
        if not self.db.sync_session._flushing:
            await self.db.flush()
        return case
