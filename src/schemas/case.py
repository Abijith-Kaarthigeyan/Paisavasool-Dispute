from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr


class CaseBase(BaseModel):
    customer_email: EmailStr
    email_subject: str | None = None
    email_body: str | None = None
    original_message_id: str | None = None


class CaseCreate(CaseBase):
    pass


class CaseAttachmentDTO(BaseModel):
    filename: str
    mime_type: str
    storage_path: str


class CaseIntakeRequest(BaseModel):
    customer_email: EmailStr
    email_subject: str | None = None
    email_body: str | None = None
    message_id: str | None = None
    gmail_thread_id: str | None = None
    rfc_message_id: str | None = None
    in_reply_to: str | None = None
    references: str | None = None
    raw_content: str | None = None
    received_at: str | None = None
    attachments: list[CaseAttachmentDTO] | None = None


class CaseResponse(CaseBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    case_number: str
    status: str
    created_at: datetime
    updated_at: datetime
