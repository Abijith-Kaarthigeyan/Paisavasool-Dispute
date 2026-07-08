import base64
from uuid import UUID, uuid4

from src.core.exceptions.business_exceptions import (
    CaseAttachmentNotFoundException,
    CaseNotFoundException,
    ValidationException,
)
from src.core.storage.attachment_storage import get_attachment_storage
from src.data.models.postgres.case_attachment import CaseAttachment
from src.data.repositories.case_attachment_repository import CaseAttachmentRepository
from src.data.repositories.case_repository import CaseRepository
from src.observability.logging.logger import logger
from src.schemas.case import CaseAttachmentDTO


class CaseAttachmentService:
    def __init__(
        self,
        case_repo: CaseRepository,
        attachment_repo: CaseAttachmentRepository,
    ):
        self.case_repo = case_repo
        self.attachment_repo = attachment_repo
        self._storage = get_attachment_storage()

    async def persist_attachments(
        self,
        case_id: UUID,
        attachments: list[CaseAttachmentDTO],
    ) -> list[CaseAttachment]:
        if not attachments:
            return []

        saved: list[CaseAttachment] = []
        for attachment in attachments:
            relative_path = f"{case_id}/{uuid4()}_{attachment.filename}"

            if attachment.content_base64:
                try:
                    file_bytes = base64.b64decode(attachment.content_base64)
                except Exception as exc:
                    raise ValidationException(
                        f"Invalid base64 content for attachment {attachment.filename}"
                    ) from exc
                self._storage.write_bytes(relative_path, file_bytes)
            elif attachment.storage_path:
                from pathlib import Path

                source = Path(attachment.storage_path)
                if not source.is_file():
                    raise ValidationException(
                        f"Attachment source file not found: {attachment.storage_path}"
                    )
                self._storage.write_bytes(relative_path, source.read_bytes())
            else:
                raise ValidationException(
                    f"Attachment {attachment.filename} requires content_base64 or storage_path"
                )

            saved.append(
                await self.attachment_repo.create_attachment(
                    case_id=case_id,
                    filename=attachment.filename,
                    mime_type=attachment.mime_type,
                    file_path=relative_path,
                )
            )
        return saved

    async def list_attachments(self, case_id: UUID) -> list[CaseAttachment]:
        case = await self.case_repo.get_by_id(case_id)
        if not case:
            raise CaseNotFoundException(f"Case {case_id} not found.")
        return await self.attachment_repo.list_by_case_id(case_id)

    async def merge_attachments_to_case(
        self,
        source_case_id: UUID,
        target_case_id: UUID,
    ) -> list[CaseAttachment]:
        """Copies attachments from a follow-up intake case onto the dispute's primary case."""
        if source_case_id == target_case_id:
            return await self.list_attachments(target_case_id)

        target_case = await self.case_repo.get_by_id(target_case_id)
        if not target_case:
            raise CaseNotFoundException(f"Case {target_case_id} not found.")

        source_attachments = await self.attachment_repo.list_by_case_id(source_case_id)
        if not source_attachments:
            return await self.list_attachments(target_case_id)

        existing = await self.attachment_repo.list_by_case_id(target_case_id)
        existing_filenames = {attachment.filename for attachment in existing}

        for attachment in source_attachments:
            if attachment.filename in existing_filenames:
                continue

            if not self._storage.exists(attachment.file_path):
                logger.warning(
                    "Skipping attachment merge for missing file: %s",
                    attachment.file_path,
                )
                continue

            relative_path = f"{target_case_id}/{uuid4()}_{attachment.filename}"
            self._storage.copy(attachment.file_path, relative_path)
            await self.attachment_repo.create_attachment(
                case_id=target_case_id,
                filename=attachment.filename,
                mime_type=attachment.mime_type,
                file_path=relative_path,
            )
            existing_filenames.add(attachment.filename)

        return await self.list_attachments(target_case_id)

    async def get_attachment_file(
        self, case_id: UUID, attachment_id: UUID
    ) -> tuple[bytes, CaseAttachment]:
        attachment = await self.attachment_repo.get_by_id(attachment_id)
        if not attachment or attachment.case_id != case_id:
            raise CaseAttachmentNotFoundException(
                f"Attachment {attachment_id} not found for case {case_id}."
            )

        if not self._storage.exists(attachment.file_path):
            raise CaseAttachmentNotFoundException(
                f"Attachment file missing on disk for {attachment_id}."
            )
        return self._storage.read_bytes(attachment.file_path), attachment
