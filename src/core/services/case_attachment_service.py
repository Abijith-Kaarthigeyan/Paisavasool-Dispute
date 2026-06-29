import base64
import shutil
from pathlib import Path
from uuid import UUID, uuid4

from src.core.config.settings import settings
from src.core.exceptions.business_exceptions import (
    CaseAttachmentNotFoundException,
    CaseNotFoundException,
    ValidationException,
)
from src.data.models.postgres.case_attachment import CaseAttachment
from src.data.repositories.case_attachment_repository import CaseAttachmentRepository
from src.data.repositories.case_repository import CaseRepository
from src.schemas.case import CaseAttachmentDTO


class CaseAttachmentService:
    def __init__(
        self,
        case_repo: CaseRepository,
        attachment_repo: CaseAttachmentRepository,
    ):
        self.case_repo = case_repo
        self.attachment_repo = attachment_repo

    def _storage_root(self) -> Path:
        return Path(settings.CASE_ATTACHMENT_STORAGE_DIR)

    def _resolve_disk_path(self, relative_path: str) -> Path:
        return self._storage_root() / relative_path

    async def persist_attachments(
        self,
        case_id: UUID,
        attachments: list[CaseAttachmentDTO],
    ) -> list[CaseAttachment]:
        if not attachments:
            return []

        case_dir = self._storage_root() / str(case_id)
        case_dir.mkdir(parents=True, exist_ok=True)

        saved: list[CaseAttachment] = []
        for attachment in attachments:
            relative_path = f"{case_id}/{uuid4()}_{attachment.filename}"
            destination = self._resolve_disk_path(relative_path)

            if attachment.content_base64:
                try:
                    file_bytes = base64.b64decode(attachment.content_base64)
                except Exception as exc:
                    raise ValidationException(
                        f"Invalid base64 content for attachment {attachment.filename}"
                    ) from exc
                destination.write_bytes(file_bytes)
            elif attachment.storage_path:
                source = Path(attachment.storage_path)
                if not source.is_file():
                    raise ValidationException(
                        f"Attachment source file not found: {attachment.storage_path}"
                    )
                shutil.copy2(source, destination)
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

    async def get_attachment_file(
        self, case_id: UUID, attachment_id: UUID
    ) -> tuple[Path, CaseAttachment]:
        attachment = await self.attachment_repo.get_by_id(attachment_id)
        if not attachment or attachment.case_id != case_id:
            raise CaseAttachmentNotFoundException(
                f"Attachment {attachment_id} not found for case {case_id}."
            )

        disk_path = self._resolve_disk_path(attachment.file_path)
        if not disk_path.is_file():
            raise CaseAttachmentNotFoundException(
                f"Attachment file missing on disk for {attachment_id}."
            )
        return disk_path, attachment
