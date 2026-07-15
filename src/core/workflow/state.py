"""LangGraph state schema for dispute workflow."""

from typing import Any, TypedDict
from uuid import UUID


class DisputeWorkflowState(TypedDict):
    """Workflow state model for LangGraph dispute processing."""

    case_id: UUID | None
    dispute_id: UUID | None
    dispute_number: str | None
    invoice_id: UUID | None
    invoice_number: str | None
    customer_id: UUID | None
    customer_email: str | None
    message_id: str | None
    gmail_thread_id: str | None
    rfc_message_id: str | None
    in_reply_to: str | None
    email_references: str | None
    email_subject: str | None
    email_body: str | None
    raw_content: str | None
    dispute_category: str | None
    assigned_to: UUID | None
    resolution_outcome: str | None
    workflow_status: str
    current_node: str
    requires_human_review: bool
    review_reason: str | None
    errors: list[str]
    invoices: list[dict[str, Any]] | None
    confidence: float | None
    metadata: dict[str, Any]
