"""LangGraph state schema for dispute workflow."""

from typing import Any, TypedDict, Optional
from uuid import UUID


class DisputeWorkflowState(TypedDict):
    """Workflow state model for LangGraph dispute processing."""

    case_id: Optional[UUID]
    dispute_id: Optional[UUID]
    dispute_number: Optional[str]
    invoice_id: Optional[UUID]
    invoice_number: Optional[str]
    customer_id: Optional[UUID]
    customer_email: Optional[str]
    message_id: Optional[str]
    thread_id: Optional[str]
    email_subject: Optional[str]
    email_body: Optional[str]
    raw_content: Optional[str]
    dispute_category: Optional[str]
    assigned_to: Optional[UUID]
    resolution_outcome: Optional[str]
    workflow_status: str
    current_node: str
    requires_human_review: bool
    review_reason: Optional[str]
    errors: list[str]
    invoices: Optional[list[dict[str, Any]]]
    confidence: Optional[float]
    metadata: dict[str, Any]
