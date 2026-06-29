from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.models.postgres.case_attachment import CaseAttachment


class CaseAttachmentRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_by_case_id(self, case_id: UUID) -> list[CaseAttachment]:
        result = await self.db.execute(
            select(CaseAttachment)
            .where(CaseAttachment.case_id == case_id)
            .order_by(CaseAttachment.created_at.asc())
        )
        return list(result.scalars().all())

    async def get_by_id(self, attachment_id: UUID) -> CaseAttachment | None:
        result = await self.db.execute(
            select(CaseAttachment).where(CaseAttachment.id == attachment_id)
        )
        return result.scalar_one_or_none()

    async def create_attachment(
        self,
        *,
        case_id: UUID,
        filename: str,
        mime_type: str,
        file_path: str,
    ) -> CaseAttachment:
        attachment = CaseAttachment(
            case_id=case_id,
            filename=filename,
            mime_type=mime_type,
            file_path=file_path,
        )
        self.db.add(attachment)
        if not self.db.sync_session._flushing:
            await self.db.flush()
        return attachment
