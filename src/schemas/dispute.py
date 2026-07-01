from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DisputeBase(BaseModel):
    case_id: UUID
    invoice_id: UUID
    invoice_number: str
    customer_id: UUID
    dispute_category: str


class DisputeCreate(DisputeBase):
    pass


class DisputeResponse(DisputeBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    dispute_number: str
    status: str
    resolution_outcome: str | None = None
    assigned_to: UUID | None = None
    manager_id: UUID | None = None
    opened_at: datetime | None = None
    resolved_at: datetime | None = None
    closed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class DisputeAssignmentRequest(BaseModel):
    assigned_to: UUID


class DisputeCommentCreate(BaseModel):
    comment: str
    comment_type: str = "INTERNAL"  # INTERNAL, CUSTOMER, SYSTEM


class DisputeCommentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    dispute_id: UUID
    comment: str
    comment_type: str
    created_by: UUID
    created_at: datetime


class DisputeActivityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    dispute_id: UUID
    activity_type: str
    metadata: dict | None = Field(None, validation_alias="activity_metadata")
    performed_by: UUID | None = None
    created_at: datetime


class DisputeRecommendationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    dispute_id: UUID
    recommended_action: str
    recommended_invoice_json: dict | None = None
    confidence: float
    created_by_agent: str
    created_at: datetime


class ReviewQueueResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    dispute_id: UUID
    review_reason: str
    assigned_to: UUID | None = None
    status: str
    error_message: str | None = None
    stack_trace: str | None = None
    retry_count: int
    created_at: datetime
    updated_at: datetime


class DisputeCommunicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    dispute_id: UUID
    recipient: str
    subject: str
    message_body: str = Field(validation_alias="body")
    communication_type: str
    gmail_message_id: str | None = None
    rfc_message_id: str | None = None
    sent_time: datetime = Field(validation_alias="created_at")
    created_at: datetime


class AssociateCommunicationDraftRequest(BaseModel):
    instructions: str | None = None


class AssociateCommunicationDraftResponse(BaseModel):
    id: UUID | None = None
    recipient: str
    subject: str
    body: str
    status: str | None = None
    created_at: datetime | None = None


class AssociateCommunicationSendRequest(BaseModel):
    recipient: str
    subject: str
    body: str


class DisputeDecisionRequest(BaseModel):
    decision: str
    comments: str | None = None
    amended_invoice_json: dict | None = None


class ReviewQueueResolveRequest(BaseModel):
    invoice_number: str
    dispute_category: str
    comments: str | None = None


class DisputeEscalateRequest(BaseModel):
    comments: str | None = None


class DisputeCloseRequest(BaseModel):
    resolution_method: Literal["PHONE", "IN_PERSON", "EMAIL", "OTHER"]
    resolution_outcome: Literal["CUSTOMER_CORRECT", "COMPANY_CORRECT"]
    comments: str = Field(..., min_length=1)
