"""LangGraph orchestration layer for dispute processing."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.errors import NodeInterrupt
from langgraph.graph import END, StateGraph

from src.core.config.settings import settings
from src.core.services.assignment_service import AssignmentService
from src.core.services.audit_service import AuditService
from src.core.services.conversation_history_service import ConversationHistoryService
from src.core.services.correlation_service import CorrelationService
from src.core.services.dispute_close_service import DisputeCloseService
from src.core.services.escalation_service import EscalationService
from src.core.services.internal_team_config_service import InternalTeamConfigService
from src.core.services.outbound_email_service import OutboundEmailService
from src.core.services.recommendation_service import RecommendationService
from src.core.services.sla_service import SLAService
from src.core.services.workflow_context_service import WorkflowContextService
from src.core.workflow.amendment_agent import AmendmentResolutionAgent
from src.core.workflow.checkpointer import DbWorkflowCheckpointer
from src.core.workflow.evidence_service import EvidenceSnapshotService
from src.core.workflow.mail_agent import DisputeMailAgent
from src.core.workflow.payment_reference_agent import PaymentReferenceExtractionAgent
from src.core.workflow.state import DisputeWorkflowState
from src.core.workflow.triage_agent import DisputeTriageAgent
from src.data.clients.ar_service_client import ARServiceClient
from src.data.repositories.assignment_repository import AssignmentRepository
from src.data.repositories.case_repository import CaseRepository
from src.data.repositories.communication_repository import CommunicationRepository
from src.data.repositories.dispute_repository import DisputeRepository
from src.data.repositories.escalation_repository import EscalationRepository
from src.data.repositories.internal_team_contact_repository import (
    InternalTeamContactRepository,
)
from src.data.repositories.other_repositories import (
    ActivityRepository,
    AgentRunRepository,
    CommentRepository,
    EvidenceSnapshotRepository,
)
from src.data.repositories.recommendation_repository import RecommendationRepository
from src.data.repositories.review_queue_repository import ReviewQueueRepository
from src.data.repositories.sla_repository import SLARepository
from src.data.repositories.user_repository import UserRepository
from src.data.repositories.workflow_context_repository import WorkflowContextRepository
from src.observability.logging.logger import logger

PAYMENT_DISPUTE_CATEGORIES = frozenset(
    {"PAYMENT_ALREADY_DONE", "PAYMENT_NOT_REFLECTED"}
)

# --- HELPERS ---


def _conversation_history_service(db: Any) -> ConversationHistoryService:
    return ConversationHistoryService(
        CommunicationRepository(db),
        CommentRepository(db),
    )


def _outbound_email_service(db: Any) -> OutboundEmailService:
    activity_repo = ActivityRepository(db)
    return OutboundEmailService(
        ar_client=ARServiceClient(),
        comm_repo=CommunicationRepository(db),
        case_repo=CaseRepository(db),
        audit_service=AuditService(activity_repo),
    )


def _dispute_close_service(db: Any) -> DisputeCloseService:
    activity_repo = ActivityRepository(db)
    audit_service = AuditService(activity_repo)
    dispute_repo = DisputeRepository(db)
    sla_repo = SLARepository(db)
    escalation_repo = EscalationRepository(db)
    context_repo = WorkflowContextRepository(db)
    return DisputeCloseService(
        dispute_repo=dispute_repo,
        sla_repo=sla_repo,
        audit_service=audit_service,
        ar_client=ARServiceClient(),
        escalation_service=EscalationService(
            escalation_repo, sla_repo, dispute_repo, audit_service
        ),
        workflow_context_service=WorkflowContextService(context_repo, audit_service),
        workflow_context_repo=context_repo,
        comment_repo=CommentRepository(db),
    )


async def _dispatch_outbound_email(
    db: Any,
    dispute: Any,
    comm: Any,
    *,
    use_thread: bool = True,
) -> None:
    await _outbound_email_service(db).send_communication(
        dispute_id=dispute.id,
        communication=comm,
        case=getattr(dispute, "case", None),
        use_thread=use_thread,
    )


async def _record_mail_agent_run(
    db: Any,
    dispute_id: UUID,
    run_details: dict[str, Any],
) -> None:
    """Persists DisputeMailAgent execution metadata."""
    agent_run_repo = AgentRunRepository(db)
    agent_run = await agent_run_repo.create_agent_run(
        dispute_id=dispute_id,
        agent_name="DisputeMailAgent",
        input_payload=run_details.get("input_payload"),
        status=run_details.get("status"),
    )
    agent_run.started_at = datetime.now() - timedelta(
        seconds=(run_details.get("latency") or 0)
    )
    agent_run.completed_at = datetime.now()
    agent_run.output_payload = {
        "prompt": run_details.get("prompt"),
        "output": run_details.get("output_payload"),
        "latency": run_details.get("latency"),
        "model": run_details.get("model"),
        "provider": run_details.get("provider"),
    }
    await agent_run_repo.update_agent_run(agent_run)


async def _persist_outbound_customer_mail(
    db: Any,
    dispute_id: UUID,
    agent_res: dict[str, Any],
) -> Any:
    """Stores a generated outbound customer email in dispute_communications."""
    comm_repo = CommunicationRepository(db)
    run_details = agent_res.get("agent_run_details")
    if run_details:
        await _record_mail_agent_run(db, dispute_id, run_details)

    return await comm_repo.create_communication(
        dispute_id=dispute_id,
        recipient=agent_res["recipient"],
        subject=agent_res["subject"],
        body=agent_res["body"],
        communication_type="CUSTOMER",
    )


def _merge_recommended_invoice_json(
    original: dict[str, Any],
    recommended: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Merges agent-recommended invoice changes onto the fetched invoice snapshot."""
    if not recommended:
        return None

    merged = {**original, **recommended}
    if recommended.get("items"):
        merged["items"] = recommended["items"]
    elif not merged.get("items"):
        merged["items"] = original.get("items", [])
    return merged


def _build_ar_amend_payload(
    invoice_json: dict[str, Any],
    *,
    dispute_id: UUID,
    recommendation_id: UUID | None,
    change_reason: str,
    created_by: str = "dispute-service",
) -> dict[str, Any]:
    """Maps dispute recommendation JSON to AR amend API request body."""
    items = invoice_json.get("items") or invoice_json.get("invoice_items") or []
    return {
        "subtotal_amount": float(invoice_json.get("subtotal_amount", 0)),
        "tax_amount": float(invoice_json.get("tax_amount", 0)),
        "total_amount": float(invoice_json.get("total_amount", 0)),
        "invoice_date": invoice_json.get("invoice_date"),
        "due_date": invoice_json.get("due_date"),
        "currency": invoice_json.get("currency"),
        "items": [
            {
                "description": str(
                    item.get("description") or item.get("product_name") or ""
                ),
                "quantity": float(item.get("quantity", 0)),
                "unit_price": float(item.get("unit_price", 0)),
                "amount": float(item.get("amount", item.get("line_amount", 0))),
            }
            for item in items
        ],
        "change_reason": change_reason,
        "change_source": "DISPUTE_AMENDMENT",
        "dispute_id": str(dispute_id),
        "recommendation_id": str(recommendation_id) if recommendation_id else None,
        "created_by": created_by,
    }


async def _persist_inbound_customer_email(
    *,
    db: Any,
    dispute_id: UUID,
    customer_email: str,
    email_subject: str,
    email_body: str,
) -> UUID | None:
    """Stores the original customer email on the dispute if not already recorded."""
    if not (email_subject or "").strip() and not (email_body or "").strip():
        return None

    comm_repo = CommunicationRepository(db)
    comms = await comm_repo.get_communications_for_dispute(dispute_id)
    normalized_subject = (email_subject or "").strip()
    normalized_body = email_body or ""
    already_recorded = any(
        c.communication_type == "CUSTOMER"
        and (c.subject or "").strip() == normalized_subject
        and c.body == normalized_body
        for c in comms
    )
    if already_recorded:
        return None

    comm = await comm_repo.create_communication(
        dispute_id=dispute_id,
        recipient=customer_email or "customer@example.com",
        subject=normalized_subject or "(No Subject)",
        body=normalized_body,
        communication_type="CUSTOMER",
    )

    return comm.id


def _associate_draft_metadata(
    dispute_id: UUID, source_communication_id: UUID
) -> dict[str, str]:
    return {
        "dispute_id": str(dispute_id),
        "source_communication_id": str(source_communication_id),
    }


async def _notify_customer_need_more_info(
    *,
    db: Any,
    dispute: Any,
    state: DisputeWorkflowState,
    info_request: str,
) -> None:
    """Generates and persists a customer email describing what additional info is needed."""
    await _send_customer_outbound_mail(
        db=db,
        dispute_id=dispute.id,
        dispute=dispute,
        state=state,
        outcome="NEED_MORE_INFO",
        info_request=info_request,
    )


async def _send_customer_outbound_mail(
    *,
    db: Any,
    dispute_id: UUID,
    dispute: Any,
    state: DisputeWorkflowState,
    outcome: str,
    info_request: str | None = None,
) -> None:
    """Generates and persists a customer-facing email for the given workflow outcome."""
    activity_repo = ActivityRepository(db)
    comments_repo = CommentRepository(db)

    activities = await activity_repo.list_activities_for_dispute(dispute_id)
    act_summary = "\n".join(
        [f"- {a.activity_type}: {a.activity_metadata}" for a in activities]
    )

    comments = await comments_repo.list_comments_for_dispute(dispute_id)
    comm_summary = "\n".join([f"- {c.comment_type}: {c.comment}" for c in comments])

    metadata = dict(state.get("metadata") or {})
    invoice_json = metadata.get("invoice_json", {})
    recipient = state.get("customer_email") or "customer@example.com"
    invoice_number = dispute.invoice_number or state.get("invoice_number")
    payment_reference = metadata.get("extracted_reference_number")

    resolution_reason = None
    for activity in reversed(activities):
        if (
            activity.activity_type == "PAYMENT_OUTCOME_PROPOSED"
            and activity.activity_metadata
        ):
            resolution_reason = activity.activity_metadata.get("reason")
            break

    customer_message_summary = await _conversation_history_service(
        db
    ).build_customer_conversation_text(
        dispute_id,
        dispute,
        state_fallback=state,
    )

    agent_res = await DisputeMailAgent.generate_mail(
        dispute_category=dispute.dispute_category,
        outcome=outcome,
        customer_email=recipient,
        activities_summary=act_summary,
        comments_summary=comm_summary,
        invoice_summary=json.dumps(invoice_json),
        info_request=info_request,
        invoice_number=invoice_number,
        customer_message_summary=customer_message_summary,
        payment_reference=payment_reference,
        resolution_reason=resolution_reason,
    )

    comm = await _persist_outbound_customer_mail(db, dispute_id, agent_res)
    await _dispatch_outbound_email(db, dispute, comm, use_thread=True)


async def case_intake_node(
    state: DisputeWorkflowState, config: RunnableConfig
) -> dict[str, Any]:
    """Idempotently processes case intake and persists customer communication."""
    logger.info("[Node Start] case_intake_node")
    db = config["configurable"]["db"]

    # If dispute_id is set, this is a single dispute workflow resumption/run - bypass intake nodes
    if state.get("dispute_id"):
        logger.info("[Node End] case_intake_node - Bypassed")
        return {}

    message_id = state.get("message_id")
    case_repo = CaseRepository(db)

    # Check if a case with this message_id already exists to ensure idempotency
    if message_id:
        existing_case = await case_repo.find_by_original_message_id(message_id)
        if existing_case:
            current_thread_id = config["configurable"].get("thread_id")
            if current_thread_id and str(existing_case.id) == current_thread_id:
                logger.info(
                    "Processing current case %s (%s)",
                    existing_case.id,
                    existing_case.case_number,
                )
                return {
                    "case_id": existing_case.id,
                    "workflow_status": "CASE_CREATED",
                    "current_node": "case_intake_node",
                }
            else:
                logger.info(
                    "Intake Idempotency triggered. Reusing existing case %s (%s)",
                    existing_case.id,
                    existing_case.case_number,
                )
                meta = dict(state.get("metadata") or {})
                meta["is_idempotent_bypass"] = True
                return {
                    "case_id": existing_case.id,
                    "workflow_status": "CASE_CREATED",
                    "current_node": "case_intake_node",
                    "metadata": meta,
                }

    # Generate sequential Case Number: CASE-YYYY-000001
    year = datetime.now().year
    count = await case_repo.count_cases()
    case_number = f"CASE-{year}-{count + 1:06d}"

    case = await case_repo.create_case(
        case_number=case_number,
        customer_email=state["customer_email"],
        email_subject=state.get("email_subject"),
        email_body=state.get("email_body"),
        original_message_id=message_id,
        gmail_thread_id=state.get("gmail_thread_id"),
        rfc_message_id=state.get("rfc_message_id"),
        raw_content=state.get("raw_content"),
    )
    logger.info("[Node End] case_intake_node - Created case: %s", case.case_number)
    return {
        "case_id": case.id,
        "workflow_status": "CASE_CREATED",
        "current_node": "case_intake_node",
    }


async def triage_agent_node(
    state: DisputeWorkflowState, config: RunnableConfig
) -> dict[str, Any]:
    """Runs the DisputeTriageAgent to extract invoices and categories."""
    logger.info("[Node Start] triage_agent_node")
    if state.get("dispute_id"):
        logger.info("[Node End] triage_agent_node - Bypassed")
        return {}

    subject = state.get("email_subject") or ""
    body = state.get("email_body") or ""
    raw_content = state.get("raw_content") or ""

    triage_res = await DisputeTriageAgent.triage_communication(
        subject, body, raw_content
    )

    # Save the triage execution context in metadata for later persistence into dispute_agent_runs
    metadata = state.get("metadata") or {}
    metadata["triage_run"] = triage_res.get("agent_run_details")

    logger.info(
        "[Node End] triage_agent_node - Extracted %s invoices",
        len(triage_res["invoices"]),
    )
    return {
        "invoices": triage_res["invoices"],
        "confidence": triage_res["confidence"],
        "requires_human_review": triage_res["confidence"] < 80.0,
        "review_reason": "LOW_CONFIDENCE_TRIAGE"
        if triage_res["confidence"] < 80.0
        else None,
        "metadata": metadata,
        "current_node": "triage_agent_node",
    }


async def pre_correlation_node(
    state: DisputeWorkflowState, config: RunnableConfig
) -> dict[str, Any]:
    """Correlates incoming case email to an existing dispute before generating new ones."""
    logger.info("[Node Start] pre_correlation_node")
    if state.get("dispute_id"):
        logger.info("[Node End] pre_correlation_node - Bypassed")
        return {}

    db = config["configurable"]["db"]
    dispute_repo = DisputeRepository(db)
    comm_repo = CommunicationRepository(db)
    comment_repo = CommentRepository(db)
    case_repo = CaseRepository(db)
    activity_repo = ActivityRepository(db)
    audit_service = AuditService(activity_repo)

    correlation_service = CorrelationService(
        dispute_repo=dispute_repo,
        communication_repo=comm_repo,
        comment_repo=comment_repo,
        audit_service=audit_service,
        case_repo=case_repo,
    )

    correlated = await correlation_service.find_correlated_dispute_for_intake(
        invoices=state.get("invoices") or [],
        customer_email=state.get("customer_email") or "",
        email_subject=state.get("email_subject") or "",
        email_body=state.get("email_body") or "",
        raw_content=state.get("raw_content") or "",
        gmail_thread_id=state.get("gmail_thread_id"),
        in_reply_to=state.get("in_reply_to"),
        email_references=state.get("email_references"),
    )

    if correlated:
        logger.info(
            "Pre-correlation matched existing dispute %s. Skipping dispute generation.",
            correlated.dispute_id,
        )
        metadata = dict(state.get("metadata") or {})
        metadata["resume_dispute_id"] = str(correlated.dispute_id)
        metadata["associate_draft"] = _associate_draft_metadata(
            correlated.dispute_id, correlated.source_communication_id
        )
        return {
            "workflow_status": "CORRELATED_EXISTING",
            "current_node": "pre_correlation_node",
            "metadata": metadata,
        }

    logger.info("[Node End] pre_correlation_node - No existing dispute match")
    return {
        "workflow_status": "NEW_INTAKE",
        "current_node": "pre_correlation_node",
    }


async def dispute_generation_node(
    state: DisputeWorkflowState, config: RunnableConfig
) -> dict[str, Any]:
    """Creates independent dispute records and schedules Celery task workflows."""
    logger.info("[Node Start] dispute_generation_node")
    if state.get("dispute_id"):
        logger.info("[Node End] dispute_generation_node - Bypassed")
        return {}

    db = config["configurable"]["db"]
    case_id = state.get("case_id")
    invoices = state.get("invoices") or []
    requires_review = state.get("requires_human_review", False)
    review_reason = state.get("review_reason")

    dispute_repo = DisputeRepository(db)
    activity_repo = ActivityRepository(db)
    agent_run_repo = AgentRunRepository(db)
    sla_repo = SLARepository(db)

    # Instantiate services
    audit_service = AuditService(activity_repo)
    sla_service = SLAService(sla_repo, dispute_repo, audit_service, settings)

    assign_repo = AssignmentRepository(db)
    u_repo = UserRepository(db)
    assignment_service = AssignmentService(
        dispute_repo, assign_repo, u_repo, audit_service
    )

    generated_disputes = []
    ar_client = ARServiceClient()

    # Map granularity and create disputes: One invoice + one category = one dispute
    for inv in invoices:
        inv_num = inv["invoice_number"]
        dispute_types = inv["dispute_types"]

        invoice = await ar_client.lookup_invoice_by_number(inv_num)
        if invoice:
            invoice_id = UUID(str(invoice["id"]))
            customer_id = UUID(str(invoice["customer_id"]))
            invoice_exists = True
            invoice_cancelled = invoice.get("status") == "CANCELLED"
        else:
            invoice_id = uuid4()
            customer_id = uuid4()
            invoice_exists = False
            invoice_cancelled = False

        for category in dispute_types:
            normalized_cat = DisputeTriageAgent.normalize_category(category)

            year = datetime.now().year
            count = await dispute_repo.count_disputes()
            dispute_number = f"DISP-{year}-{count + 1:06d}"

            # Create Dispute DB record
            dispute = await dispute_repo.create_dispute(
                dispute_number=dispute_number,
                case_id=case_id,
                invoice_id=invoice_id,
                invoice_number=inv_num,
                customer_id=customer_id,
                dispute_category=normalized_cat,
                status="OPEN",
            )

            # 1. Create SLA
            await sla_service.create_sla(dispute.id)

            # 2. Trigger automatic assignment engine
            await assignment_service.assign_dispute(dispute.id)

            # 3. Log agent run if triage LLM was executed
            triage_details = state.get("metadata", {}).get("triage_run")
            if triage_details:
                agent_run = await agent_run_repo.create_agent_run(
                    dispute_id=dispute.id,
                    agent_name="DisputeTriageAgent",
                    input_payload=triage_details.get("input_payload"),
                    status=triage_details.get("status"),
                )
                agent_run.started_at = datetime.now() - timedelta(
                    seconds=(triage_details.get("latency") or 0)
                )
                agent_run.completed_at = datetime.now()
                # Store prompt and latency in outputs
                agent_run.output_payload = {
                    "prompt": triage_details.get("prompt"),
                    "output": triage_details.get("output_payload"),
                    "latency": triage_details.get("latency"),
                    "model": triage_details.get("model"),
                    "provider": triage_details.get("provider"),
                }
                await agent_run_repo.update_agent_run(agent_run)

            # If invoice doesn't exist or is cancelled, or triage was low confidence, mark for review queue
            dispute_review_needed = requires_review
            dispute_review_reason = review_reason

            if not invoice_exists:
                dispute_review_needed = True
                dispute_review_reason = "INVOICE_MISSING"
            elif invoice_cancelled:
                dispute_review_needed = True
                dispute_review_reason = "INVOICE_CANCELLED"

            if dispute_review_needed:
                dispute.status = "IN_REVIEW"
                await dispute_repo.update_dispute(dispute)
                # Create review queue entry
                review_repo = ReviewQueueRepository(db)
                await review_repo.create_review_queue_item(
                    dispute_id=dispute.id,
                    review_reason=dispute_review_reason or "TRIAGE_REVIEW",
                    status="PENDING_REVIEW",
                )
                # Log review activity
                await audit_service.log_event(
                    dispute_id=dispute.id,
                    action="WORKFLOW_INTERRUPTED",
                    metadata={"reason": dispute_review_reason},
                )

            # 4. Spawns process_dispute_workflow Celery task
            from src.infrastructure.celery.tasks import process_dispute_workflow

            process_dispute_workflow.delay(str(dispute.id))

            generated_disputes.append(dispute.id)

    logger.info(
        "[Node End] dispute_generation_node - Generated %s disputes",
        len(generated_disputes),
    )
    return {
        "workflow_status": "DISPUTES_GENERATED",
        "current_node": "dispute_generation_node",
    }


async def correlation_node(
    state: DisputeWorkflowState, config: RunnableConfig
) -> dict[str, Any]:
    """Correlates dispute details against active dispute workflows."""
    logger.info("[Node Start] correlation_node")
    if not state.get("dispute_id"):
        logger.info("[Node End] correlation_node - Bypassed")
        return {}

    db = config["configurable"]["db"]
    dispute_id = state["dispute_id"]

    dispute_repo = DisputeRepository(db)
    comm_repo = CommunicationRepository(db)
    comment_repo = CommentRepository(db)
    activity_repo = ActivityRepository(db)
    audit_service = AuditService(activity_repo)

    correlation_service = CorrelationService(
        dispute_repo=dispute_repo,
        communication_repo=comm_repo,
        comment_repo=comment_repo,
        audit_service=audit_service,
    )

    correlated = await correlation_service.correlate_dispute(
        invoice_number=state.get("invoice_number"),
        raw_category=state.get("dispute_category"),
        customer_email=state.get("customer_email"),
        email_subject=state.get("email_subject") or "",
        email_body=state.get("email_body") or "",
        exclude_dispute_id=dispute_id,
    )

    if correlated and correlated.dispute_id != dispute_id:
        logger.info(
            "Correlated to existing active dispute: %s. Cancelling this workflow.",
            correlated.dispute_id,
        )
        # Update current dispute to duplicate/cancelled
        dispute = await dispute_repo.get_by_id(dispute_id)
        if dispute:
            dispute.status = "CANCELLED"
            dispute.resolution_outcome = f"DUPLICATE_OF_{correlated.dispute_id}"
            await dispute_repo.update_dispute(dispute)

            # Log cancel event
            await audit_service.log_event(
                dispute_id=dispute_id,
                action="STATUS_CHANGED",
                metadata={
                    "old_status": "OPEN",
                    "new_status": "CANCELLED",
                    "reason": "CORRELATED_DUPLICATE",
                },
            )

        metadata = dict(state.get("metadata") or {})
        metadata["resume_dispute_id"] = str(correlated.dispute_id)
        metadata["associate_draft"] = _associate_draft_metadata(
            correlated.dispute_id, correlated.source_communication_id
        )
        return {
            "workflow_status": "CANCELLED_DUPLICATE",
            "resolution_outcome": f"DUPLICATE_OF_{correlated.dispute_id}",
            "current_node": "correlation_node",
            "metadata": metadata,
        }

    logger.info("[Node End] correlation_node - Verified as NEW_DISPUTE")
    source_communication_id = await _persist_inbound_customer_email(
        db=db,
        dispute_id=dispute_id,
        customer_email=state.get("customer_email") or "",
        email_subject=state.get("email_subject") or "",
        email_body=state.get("email_body") or "",
    )
    metadata = dict(state.get("metadata") or {})
    if source_communication_id:
        metadata["associate_draft"] = _associate_draft_metadata(
            dispute_id, source_communication_id
        )
    return {
        "workflow_status": "NEW_DISPUTE",
        "current_node": "correlation_node",
        "metadata": metadata,
    }


async def assignment_node(
    state: DisputeWorkflowState, config: RunnableConfig
) -> dict[str, Any]:
    """Ensures dispute is assigned to an associate and manager."""
    logger.info("[Node Start] assignment_node")
    if (
        not state.get("dispute_id")
        or state.get("workflow_status") == "CANCELLED_DUPLICATE"
    ):
        logger.info("[Node End] assignment_node - Bypassed")
        return {}

    db = config["configurable"]["db"]
    dispute_id = state["dispute_id"]

    dispute_repo = DisputeRepository(db)
    activity_repo = ActivityRepository(db)
    audit_service = AuditService(activity_repo)
    assign_repo = AssignmentRepository(db)
    u_repo = UserRepository(db)
    assignment_service = AssignmentService(
        dispute_repo, assign_repo, u_repo, audit_service
    )

    dispute = await dispute_repo.get_by_id(dispute_id)
    if dispute and not dispute.assigned_to:
        dispute = await assignment_service.assign_dispute(dispute_id)

    assigned_to = dispute.assigned_to if dispute else None
    logger.info("[Node End] assignment_node - Assigned to: %s", assigned_to)
    return {
        "assigned_to": assigned_to,
        "current_node": "assignment_node",
    }


async def validation_node(
    state: DisputeWorkflowState, config: RunnableConfig
) -> dict[str, Any]:
    """Validates if invoice exists and is not cancelled."""
    logger.info("[Node Start] validation_node")
    if (
        not state.get("dispute_id")
        or state.get("workflow_status") == "CANCELLED_DUPLICATE"
    ):
        logger.info("[Node End] validation_node - Bypassed")
        return {}

    db = config["configurable"]["db"]
    dispute_id = state["dispute_id"]

    dispute_repo = DisputeRepository(db)
    dispute = await dispute_repo.get_by_id(dispute_id)
    if not dispute:
        return {}

    ar_client = ARServiceClient()
    invoice = await ar_client.lookup_invoice_by_number(dispute.invoice_number)

    requires_review = False
    reason = None

    if not invoice:
        requires_review = True
        reason = "INVOICE_MISSING"
    else:
        dispute.invoice_id = UUID(str(invoice["id"]))
        await dispute_repo.update_dispute(dispute)
        if invoice.get("status") == "CANCELLED":
            requires_review = True
            reason = "INVOICE_CANCELLED"

    logger.info(
        "[Node End] validation_node - Requires review: %s (%s)", requires_review, reason
    )
    return {
        "invoice_id": dispute.invoice_id if invoice else state.get("invoice_id"),
        "requires_human_review": requires_review,
        "review_reason": reason,
        "current_node": "validation_node",
    }


async def review_queue_node(
    state: DisputeWorkflowState, config: RunnableConfig
) -> dict[str, Any]:
    """Puts dispute in the review queue and raises human interrupt."""
    logger.info("[Node Start] review_queue_node")
    if not state.get("dispute_id") or not state.get("requires_human_review"):
        logger.info("[Node End] review_queue_node - Bypassed")
        return {}

    db = config["configurable"]["db"]
    dispute_id = state["dispute_id"]
    reason = state["review_reason"] or "VALIDATION_FAILED"

    # Update dispute status
    dispute_repo = DisputeRepository(db)
    dispute = await dispute_repo.get_by_id(dispute_id)
    if dispute:
        dispute.status = "IN_REVIEW"
        await dispute_repo.update_dispute(dispute)

    # Idempotently check/create review queue entry
    review_repo = ReviewQueueRepository(db)
    existing_item = await review_repo.get_by_dispute_id(dispute_id)
    if not existing_item:
        await review_repo.create_review_queue_item(
            dispute_id=dispute_id,
            review_reason=reason,
            status="PENDING_REVIEW",
        )

    # Log review/pause activity
    activity_repo = ActivityRepository(db)
    audit_service = AuditService(activity_repo)
    await audit_service.log_event(
        dispute_id=dispute_id,
        action="WORKFLOW_INTERRUPTED",
        metadata={"reason": reason},
    )

    logger.info("[Interrupt raised] review_queue_node")
    raise NodeInterrupt(f"Workflow paused at Review Queue: {reason}")


async def routing_node(
    state: DisputeWorkflowState, config: RunnableConfig
) -> dict[str, Any]:
    """Captures evidence snapshots and determines routing paths."""
    logger.info("[Node Start] routing_node")
    if (
        not state.get("dispute_id")
        or state.get("workflow_status") == "CANCELLED_DUPLICATE"
    ):
        logger.info("[Node End] routing_node - Bypassed")
        return {}

    db = config["configurable"]["db"]
    dispute_id = state["dispute_id"]

    # 1. Capture Evidence Snapshot
    dispute_repo = DisputeRepository(db)
    dispute = await dispute_repo.get_by_id(dispute_id)

    email_snap = {
        "customer_email": state.get("customer_email"),
        "email_subject": state.get("email_subject"),
        "email_body": state.get("email_body"),
        "message_id": state.get("message_id"),
    }

    ar_client = ARServiceClient()
    raw_inv_id = dispute.invoice_id if dispute else state.get("invoice_id")
    invoice_snap: dict[str, Any] = {}
    if raw_inv_id:
        try:
            invoice_snap = await ar_client.get_invoice(UUID(str(raw_inv_id)))
        except Exception:
            invoice_snap = {}

    validation_snap = {
        "validated_at": datetime.now(UTC).isoformat(),
        "status": "VALID",
        "invoice_number": state.get("invoice_number"),
        "invoice_id": str(state.get("invoice_id")),
    }

    snap_repo = EvidenceSnapshotRepository(db)
    evidence_service = EvidenceSnapshotService(snap_repo)
    await evidence_service.capture_snapshots(
        dispute_id=dispute_id,
        email_snapshot=email_snap,
        invoice_snapshot=invoice_snap,
        validation_snapshot=validation_snap,
    )

    # 2. Determine Route
    route = cls_determine_route(
        dispute.dispute_category if dispute else state.get("dispute_category")
    )
    logger.info("[Node End] routing_node - Route determined: %s", route)
    return {
        "dispute_category": dispute.dispute_category
        if dispute
        else state.get("dispute_category"),
        "workflow_status": f"ROUTED_{route}",
        "current_node": "routing_node",
    }


# --- PHASE 1C RESOLUTION PATH NODES ---


async def collections_node(
    state: DisputeWorkflowState, config: RunnableConfig
) -> dict[str, Any]:
    """Pauses collections activity for the dispute's invoice."""
    logger.info("[Node Start] collections_node")
    db = config["configurable"]["db"]
    dispute_id = state["dispute_id"]

    dispute_repo = DisputeRepository(db)
    dispute = await dispute_repo.get_by_id(dispute_id)
    if not dispute:
        return {}

    # Call AR Service to pause collections
    ar_client = ARServiceClient()
    try:
        await ar_client.pause_collections(dispute.invoice_id)
    except Exception as e:
        logger.error("Failed to pause collections in AR service: %s", str(e))

    activity_repo = ActivityRepository(db)
    audit_service = AuditService(activity_repo)
    await audit_service.log_event(
        dispute_id=dispute_id,
        action="STATUS_CHANGED",
        metadata={"field": "collections_paused", "invoice_id": str(dispute.invoice_id)},
    )

    return {
        "workflow_status": "COLLECTIONS_PAUSED",
        "current_node": "collections_node",
    }


async def invoice_fetcher_node(
    state: DisputeWorkflowState, config: RunnableConfig
) -> dict[str, Any]:
    """Fetches full invoice details and stores evidence snapshot."""
    logger.info("[Node Start] invoice_fetcher_node")
    db = config["configurable"]["db"]
    dispute_id = state["dispute_id"]

    dispute_repo = DisputeRepository(db)
    dispute = await dispute_repo.get_by_id(dispute_id)
    if not dispute:
        return {}

    ar_client = ARServiceClient()
    inv_details = await ar_client.get_invoice_details(dispute.invoice_id)

    formatted_details = {
        "invoice_number": inv_details.get("invoice_number", ""),
        "customer_name": inv_details.get("customer", {}).get("customer_name", "")
        if isinstance(inv_details.get("customer"), dict)
        else inv_details.get("customer_name_original", ""),
        "invoice_date": inv_details.get("invoice_date", ""),
        "due_date": inv_details.get("due_date", ""),
        "subtotal_amount": float(inv_details.get("subtotal_amount", 0)),
        "tax_amount": float(inv_details.get("tax_amount", 0)),
        "total_amount": float(inv_details.get("total_amount", 0)),
        "outstanding_amount": float(inv_details.get("outstanding_amount", 0)),
        "items": inv_details.get("items", []),
    }

    snap_repo = EvidenceSnapshotRepository(db)
    await snap_repo.create_evidence_snapshot(
        dispute_id=dispute_id,
        snapshot_type="invoice_fetcher_snapshot",
        snapshot_data=formatted_details,
    )

    metadata = dict(state.get("metadata") or {})
    metadata["invoice_json"] = formatted_details

    return {
        "metadata": metadata,
        "workflow_status": "INVOICE_FETCHED",
        "current_node": "invoice_fetcher_node",
    }


async def amendment_resolution_node(
    state: DisputeWorkflowState, config: RunnableConfig
) -> dict[str, Any]:
    """Evaluates invoice amendment details using AI agent and saves recommendations."""
    logger.info("[Node Start] amendment_resolution_node")
    db = config["configurable"]["db"]
    dispute_id = state["dispute_id"]

    dispute_repo = DisputeRepository(db)
    dispute = await dispute_repo.get_by_id(dispute_id)
    if not dispute:
        return {}

    raw_customer_text = await _conversation_history_service(
        db
    ).build_customer_conversation_text(
        dispute_id,
        dispute,
        state_fallback=state,
    )

    invoice_json = state.get("metadata", {}).get("invoice_json", {})

    agent_res = await AmendmentResolutionAgent.resolve_amendment(
        raw_customer_text=raw_customer_text,
        invoice_json=invoice_json,
        dispute_category=dispute.dispute_category,
    )

    # Agent Run Tracking
    run_details = agent_res.get("agent_run_details")
    if run_details:
        agent_run_repo = AgentRunRepository(db)
        agent_run = await agent_run_repo.create_agent_run(
            dispute_id=dispute_id,
            agent_name="AmendmentResolutionAgent",
            input_payload=run_details.get("input_payload"),
            status=run_details.get("status"),
        )
        agent_run.started_at = datetime.now() - timedelta(
            seconds=(run_details.get("latency") or 0)
        )
        agent_run.completed_at = datetime.now()
        agent_run.output_payload = {
            "prompt": run_details.get("prompt"),
            "output": run_details.get("output_payload"),
            "latency": run_details.get("latency"),
            "model": run_details.get("model"),
            "provider": run_details.get("provider"),
        }
        await agent_run_repo.update_agent_run(agent_run)

    outcome = agent_res["resolution_outcome"]

    # Persist Recommendation
    rec_repo = RecommendationRepository(db)
    activity_repo = ActivityRepository(db)
    comment_repo = CommentRepository(db)
    audit_service = AuditService(activity_repo)
    rec_service = RecommendationService(rec_repo, audit_service)

    await rec_service.persist_recommendation(
        dispute_id=dispute_id,
        recommended_action=f"AMENDMENT_DECISION: {outcome}. Reason: {agent_res['reasoning']}",
        confidence=agent_res["confidence"],
        created_by_agent="AmendmentResolutionAgent",
        recommended_invoice_json=_merge_recommended_invoice_json(
            invoice_json,
            agent_res.get("recommended_invoice_json"),
        ),
    )

    if outcome == "NEED_MORE_INFO":
        info_request = agent_res["reasoning"]
        # Add internal comment
        await comment_repo.create_comment(
            dispute_id=dispute_id,
            comment=f"Need More Info: {info_request}",
            comment_type="INTERNAL",
            created_by=UUID("00000000-0000-0000-0000-000000000101"),
        )

        old_status = dispute.status
        dispute.status = "WAITING_CUSTOMER"
        dispute.resolution_outcome = "NEED_MORE_INFO"
        await dispute_repo.update_dispute(dispute)

        await _notify_customer_need_more_info(
            db=db,
            dispute=dispute,
            state=state,
            info_request=info_request,
        )

        # Pause SLA
        sla_service = SLAService(
            SLARepository(db), dispute_repo, audit_service, settings
        )
        await sla_service.handle_status_change(
            dispute_id, old_status, "WAITING_CUSTOMER"
        )

        await audit_service.log_event(
            dispute_id=dispute_id,
            action="STATUS_CHANGED",
            metadata={
                "old_status": old_status,
                "new_status": "WAITING_CUSTOMER",
                "reason": "NEED_MORE_INFO",
            },
        )

        raise NodeInterrupt(
            "Workflow paused: Waiting for customer additional information."
        )

    elif outcome == "CUSTOMER_CORRECT":
        return {
            "resolution_outcome": "CUSTOMER_CORRECT",
            "workflow_status": "WAITING_ASSOCIATE_APPROVAL",
            "current_node": "amendment_resolution_node",
        }

    else:  # COMPANY_CORRECT
        dispute.resolution_outcome = "COMPANY_CORRECT"
        await dispute_repo.update_dispute(dispute)
        return {
            "resolution_outcome": "COMPANY_CORRECT",
            "workflow_status": "COMPANY_CORRECT_DETERMINED",
            "current_node": "amendment_resolution_node",
        }


async def reference_extraction_node(
    state: DisputeWorkflowState, config: RunnableConfig
) -> dict[str, Any]:
    """Extracts payment UTR reference from customer context."""
    logger.info("[Node Start] reference_extraction_node")
    db = config["configurable"]["db"]
    dispute_id = state["dispute_id"]

    dispute_repo = DisputeRepository(db)
    dispute = await dispute_repo.get_by_id(dispute_id)
    if not dispute:
        return {}

    raw_customer_text = await _conversation_history_service(
        db
    ).build_customer_conversation_text(
        dispute_id,
        dispute,
        state_fallback=state,
    )

    agent_res = await PaymentReferenceExtractionAgent.extract_reference(
        raw_customer_text
    )

    # Agent Run Tracking
    run_details = agent_res.get("agent_run_details")
    if run_details:
        agent_run_repo = AgentRunRepository(db)
        agent_run = await agent_run_repo.create_agent_run(
            dispute_id=dispute_id,
            agent_name="PaymentReferenceExtractionAgent",
            input_payload=run_details.get("input_payload"),
            status=run_details.get("status"),
        )
        agent_run.started_at = datetime.now() - timedelta(
            seconds=(run_details.get("latency") or 0)
        )
        agent_run.completed_at = datetime.now()
        agent_run.output_payload = {
            "prompt": run_details.get("prompt"),
            "output": run_details.get("output_payload"),
            "latency": run_details.get("latency"),
            "model": run_details.get("model"),
            "provider": run_details.get("provider"),
        }
        await agent_run_repo.update_agent_run(agent_run)

    ref_num = agent_res["reference_number"]

    metadata = dict(state.get("metadata") or {})
    metadata["extracted_reference_number"] = ref_num

    return {
        "metadata": metadata,
        "workflow_status": "REFERENCE_EXTRACTED",
        "current_node": "reference_extraction_node",
    }


async def payment_checker_node(
    state: DisputeWorkflowState, config: RunnableConfig
) -> dict[str, Any]:
    """Performs deterministic checks on invoice payment and payment reference records."""
    logger.info("[Node Start] payment_checker_node")
    db = config["configurable"]["db"]
    dispute_id = state["dispute_id"]

    dispute_repo = DisputeRepository(db)
    dispute = await dispute_repo.get_by_id(dispute_id)
    if not dispute:
        return {}

    activity_repo = ActivityRepository(db)
    audit_service = AuditService(activity_repo)
    comment_repo = CommentRepository(db)

    # Resumption check: If payment checker is executed, check if customer has comments containing UTR
    # and if the state doesn't have it, extract reference dynamically
    ref_num = state.get("metadata", {}).get("extracted_reference_number")

    if not ref_num:
        raw_customer_text = await _conversation_history_service(
            db
        ).build_customer_conversation_text(
            dispute_id,
            dispute,
            state_fallback=state,
        )
        if raw_customer_text.strip():
            logger.info(
                "No stored reference on checker node. Extracting from full customer history."
            )
            agent_res = await PaymentReferenceExtractionAgent.extract_reference(
                raw_customer_text
            )
            ref_num = agent_res.get("reference_number")

            run_details = agent_res.get("agent_run_details")
            if run_details:
                agent_run_repo = AgentRunRepository(db)
                agent_run = await agent_run_repo.create_agent_run(
                    dispute_id=dispute_id,
                    agent_name="PaymentReferenceExtractionAgent",
                    input_payload=run_details.get("input_payload"),
                    status=run_details.get("status"),
                )
                agent_run.started_at = datetime.now() - timedelta(
                    seconds=(run_details.get("latency") or 0)
                )
                agent_run.completed_at = datetime.now()
                agent_run.output_payload = {
                    "prompt": run_details.get("prompt"),
                    "output": run_details.get("output_payload"),
                    "latency": run_details.get("latency"),
                    "model": run_details.get("model"),
                    "provider": run_details.get("provider"),
                }
                await agent_run_repo.update_agent_run(agent_run)

            if ref_num:
                metadata = dict(state.get("metadata") or {})
                metadata["extracted_reference_number"] = ref_num
                state["metadata"] = metadata

    ar_client = ARServiceClient()

    # Step 1: Check invoice payment status
    try:
        inv_status = await ar_client.get_payment_status(dispute.invoice_id)
        if inv_status == "PAID":
            await audit_service.log_event(
                dispute_id=dispute_id,
                action="PAYMENT_OUTCOME_PROPOSED",
                metadata={
                    "proposed_outcome": "CUSTOMER_CORRECT",
                    "reason": "invoice_paid_pending_settlement_confirmation",
                },
            )
            return {
                "resolution_outcome": "CUSTOMER_CORRECT",
                "workflow_status": "WAITING_ASSOCIATE_APPROVAL",
                "current_node": "payment_checker_node",
            }
    except Exception as e:
        logger.error("Failed to fetch payment status: %s", str(e))

    # Step 2: Reference exists?
    if not ref_num:
        # NO REFERENCE
        info_request = (
            "No payment reference or UTR was provided. "
            "Please reply with the UTR number and invoice number."
        )
        await comment_repo.create_comment(
            dispute_id=dispute_id,
            comment=info_request,
            comment_type="INTERNAL",
            created_by=UUID("00000000-0000-0000-0000-000000000101"),
        )

        old_status = dispute.status
        dispute.status = "WAITING_CUSTOMER"
        dispute.resolution_outcome = "NEED_MORE_INFO"
        await dispute_repo.update_dispute(dispute)

        await _notify_customer_need_more_info(
            db=db,
            dispute=dispute,
            state=state,
            info_request=info_request,
        )

        # Pause SLA
        sla_service = SLAService(
            SLARepository(db), dispute_repo, audit_service, settings
        )
        await sla_service.handle_status_change(
            dispute_id, old_status, "WAITING_CUSTOMER"
        )

        await audit_service.log_event(
            dispute_id=dispute_id,
            action="STATUS_CHANGED",
            metadata={
                "old_status": old_status,
                "new_status": "WAITING_CUSTOMER",
                "reason": "NEED_MORE_INFO",
            },
        )

        raise NodeInterrupt(
            "Workflow paused: Waiting for customer payment reference (UTR)."
        )

    # REFERENCE EXISTS: Search payments
    res_status = await ar_client.find_payment_reference(ref_num)
    if res_status == "SETTLED":
        await audit_service.log_event(
            dispute_id=dispute_id,
            action="PAYMENT_OUTCOME_PROPOSED",
            metadata={
                "proposed_outcome": "CUSTOMER_CORRECT",
                "reason": "payment_settled_pending_settlement_confirmation",
            },
        )
        return {
            "resolution_outcome": "CUSTOMER_CORRECT",
            "workflow_status": "WAITING_ASSOCIATE_APPROVAL",
            "current_node": "payment_checker_node",
        }
    elif res_status == "REJECTED":
        dispute.resolution_outcome = "COMPANY_CORRECT"
        dispute.status = "RESOLVED"
        await dispute_repo.update_dispute(dispute)

        await audit_service.log_event(
            dispute_id=dispute_id,
            action="STATUS_CHANGED",
            metadata={
                "old_status": dispute.status,
                "new_status": "RESOLVED",
                "outcome": "COMPANY_CORRECT",
            },
        )
        return {
            "resolution_outcome": "COMPANY_CORRECT",
            "workflow_status": "RESOLVED",
            "current_node": "payment_checker_node",
        }
    else:  # REVIEW_QUEUE
        # WAITING_INTERNAL_TEAM, PENDING_INTERNAL_REVIEW. Do NOT pause SLA.
        return {
            "resolution_outcome": "PENDING_INTERNAL_REVIEW",
            "workflow_status": "WAITING_PAYMENT_REVIEW",
            "current_node": "payment_checker_node",
        }


async def department_contact_node(
    state: DisputeWorkflowState, config: RunnableConfig
) -> dict[str, Any]:
    """Notifies relevant department and sets waiting internal team status."""
    logger.info("[Node Start] department_contact_node")
    db = config["configurable"]["db"]
    dispute_id = state["dispute_id"]

    dispute_repo = DisputeRepository(db)
    dispute = await dispute_repo.get_by_id(dispute_id)
    if not dispute:
        return {}

    category = dispute.dispute_category
    team_config = InternalTeamConfigService(InternalTeamContactRepository(db))
    target_dept = team_config.resolve_team_key(category)
    recipient = await team_config.get_email_for_team_key(target_dept)

    # Save internal notification communication
    comm_repo = CommunicationRepository(db)
    internal_comm = await comm_repo.create_communication(
        dispute_id=dispute_id,
        recipient=recipient,
        subject=f"Escalation Request: Dispute {dispute.dispute_number}",
        body=f"Dispute {dispute.dispute_number} has been escalated to {target_dept} for {category} validation.",
        communication_type="INTERNAL",
    )
    await _dispatch_outbound_email(db, dispute, internal_comm, use_thread=False)

    activity_repo = ActivityRepository(db)
    audit_service = AuditService(activity_repo)
    await audit_service.log_event(
        dispute_id=dispute_id,
        action="ESCALATED",
        metadata={"department": target_dept, "category": category},
    )

    await _send_customer_outbound_mail(
        db=db,
        dispute_id=dispute_id,
        dispute=dispute,
        state=state,
        outcome="OPERATIONAL_ESCALATION",
    )

    # Set waiting status
    return {
        "resolution_outcome": "PENDING_INTERNAL_REVIEW",
        "workflow_status": "WAITING_INTERNAL_TEAM",
        "current_node": "department_contact_node",
    }


async def apply_amendment_node(
    state: DisputeWorkflowState, config: RunnableConfig
) -> dict[str, Any]:
    """Applies approved invoice amendment to AR service before customer notification."""
    logger.info("[Node Start] apply_amendment_node")
    db = config["configurable"]["db"]
    dispute_id = state["dispute_id"]

    dispute_repo = DisputeRepository(db)
    dispute = await dispute_repo.get_by_id(dispute_id)
    if not dispute:
        return {}

    category = state.get("dispute_category") or dispute.dispute_category
    outcome = state.get("resolution_outcome") or dispute.resolution_outcome
    approved_amendment = outcome in (
        "CUSTOMER_CORRECT",
        "APPROVE",
        "EDIT_AND_APPLY",
    )
    if category != "AMENDMENT" or not approved_amendment:
        logger.info("[Node Skip] apply_amendment_node - not an approved amendment")
        return {"current_node": "apply_amendment_node"}

    activity_repo = ActivityRepository(db)
    audit_service = AuditService(activity_repo)
    rec_repo = RecommendationRepository(db)
    rec_service = RecommendationService(rec_repo, audit_service)

    amended_json = (state.get("metadata") or {}).get("amended_invoice_json")
    rec = await rec_repo.get_latest_recommendation(dispute_id)
    if not rec or not rec.recommended_invoice_json:
        if amended_json:
            rec = await rec_service.persist_recommendation(
                dispute_id=dispute_id,
                recommended_action="ASSOCIATE_EDIT_AND_APPLY",
                confidence=100.0,
                created_by_agent="ASSOCIATE",
                recommended_invoice_json=amended_json,
            )
        else:
            logger.error("No amendment recommendation found for dispute %s", dispute_id)
            await audit_service.log_event(
                dispute_id=dispute_id,
                action="AMENDMENT_APPLY_FAILED",
                metadata={"error": "No recommended_invoice_json available"},
            )
            return {
                "errors": (state.get("errors") or [])
                + ["Amendment apply failed: no recommendation payload"],
                "current_node": "apply_amendment_node",
            }

    recommendation_id = rec.id
    invoice_json = rec.recommended_invoice_json or amended_json
    change_reason = rec.recommended_action or "Dispute amendment approved"

    ar_payload = _build_ar_amend_payload(
        invoice_json,
        dispute_id=dispute_id,
        recommendation_id=recommendation_id,
        change_reason=change_reason,
    )

    ar_client = ARServiceClient()
    try:
        amend_result = await ar_client.amend_invoice(dispute.invoice_id, ar_payload)
    except Exception as e:
        logger.error("Failed to apply invoice amendment in AR: %s", str(e))
        await audit_service.log_event(
            dispute_id=dispute_id,
            action="AMENDMENT_APPLY_FAILED",
            metadata={"error": str(e)},
        )
        return {
            "errors": (state.get("errors") or []) + [f"Amendment apply failed: {e}"],
            "current_node": "apply_amendment_node",
        }

    await audit_service.log_event(
        dispute_id=dispute_id,
        action="AMENDMENT_APPLIED",
        metadata={
            "invoice_version": amend_result.get("version"),
            "invoice_status": amend_result.get("status"),
            "outstanding_amount": amend_result.get("outstanding_amount"),
            "credit_amount": amend_result.get("credit_amount"),
        },
    )

    return {
        "workflow_status": "AMENDMENT_APPLIED",
        "current_node": "apply_amendment_node",
        "metadata": {
            **(state.get("metadata") or {}),
            "amendment_result": amend_result,
        },
    }


async def mail_agent_node(
    state: DisputeWorkflowState, config: RunnableConfig
) -> dict[str, Any]:
    """Runs DisputeMailAgent to compose customer correspondence."""
    logger.info("[Node Start] mail_agent_node")
    db = config["configurable"]["db"]
    dispute_id = state["dispute_id"]

    dispute_repo = DisputeRepository(db)
    dispute = await dispute_repo.get_by_id(dispute_id)
    if not dispute:
        return {}

    outcome = state.get("resolution_outcome") or dispute.resolution_outcome or "UNKNOWN"

    await _send_customer_outbound_mail(
        db=db,
        dispute_id=dispute_id,
        dispute=dispute,
        state=state,
        outcome=outcome,
    )

    return {
        "workflow_status": "MAIL_GENERATED",
        "current_node": "mail_agent_node",
    }


async def close_dispute_node(
    state: DisputeWorkflowState, config: RunnableConfig
) -> dict[str, Any]:
    """Updates the dispute database record to CLOSED state and resumes AR collections."""
    logger.info("[Node Start] close_dispute_node")
    db = config["configurable"]["db"]
    dispute_id = state["dispute_id"]

    dispute_repo = DisputeRepository(db)
    dispute = await dispute_repo.get_by_id(dispute_id)
    if not dispute:
        return {}

    outcome = state.get("resolution_outcome") or dispute.resolution_outcome
    if outcome == "NEED_MORE_INFO":
        logger.info("[Node End] close_dispute_node - Bypassed for NEED_MORE_INFO")
        return {}

    close_service = _dispute_close_service(db)
    canonical_outcome = await close_service.execute_dispute_close(
        dispute,
        outcome,
        close_reason="AUTOMATED",
    )

    return {
        "resolution_outcome": canonical_outcome,
        "workflow_status": "CLOSED",
        "current_node": "close_dispute_node",
    }


# --- WAITING NODES ---


async def waiting_approval_node(
    state: DisputeWorkflowState, config: RunnableConfig
) -> dict[str, Any]:
    """Pauses workflow in WAITING_ASSOCIATE_APPROVAL status or handles resumption."""
    logger.info("[Node Start] waiting_approval_node")
    db = config["configurable"]["db"]
    dispute_id = state["dispute_id"]

    # Check if we are resuming from an interrupt
    outcome = state.get("resolution_outcome")

    dispute_repo = DisputeRepository(db)
    dispute = await dispute_repo.get_by_id(dispute_id)
    if not dispute:
        return {}

    activity_repo = ActivityRepository(db)
    audit_service = AuditService(activity_repo)

    if outcome in ["APPROVE", "EDIT_AND_APPLY", "REJECT"]:
        final_outcome = (
            "CUSTOMER_CORRECT"
            if outcome in ["APPROVE", "EDIT_AND_APPLY"]
            else "COMPANY_CORRECT"
        )

        old_status = dispute.status
        dispute.status = "RESOLVED"
        dispute.resolution_outcome = final_outcome
        await dispute_repo.update_dispute(dispute)

        await audit_service.log_event(
            dispute_id=dispute_id,
            action="STATUS_CHANGED",
            metadata={
                "old_status": old_status,
                "new_status": "RESOLVED",
                "outcome": final_outcome,
                "associate_decision": outcome,
            },
        )
        result: dict[str, Any] = {
            "resolution_outcome": final_outcome,
            "dispute_category": dispute.dispute_category,
            "workflow_status": "RESOLVED",
            "current_node": "waiting_approval_node",
        }
        amended = state.get("metadata", {}).get("amended_invoice_json")
        if amended:
            result["metadata"] = {
                **(state.get("metadata") or {}),
                "amended_invoice_json": amended,
            }
        return result

    # First entry: Update dispute status
    old_status = dispute.status
    dispute.status = "WAITING_ASSOCIATE_APPROVAL"
    await dispute_repo.update_dispute(dispute)

    # Log pause activity
    await audit_service.log_event(
        dispute_id=dispute_id,
        action="STATUS_CHANGED",
        metadata={"old_status": old_status, "new_status": "WAITING_ASSOCIATE_APPROVAL"},
    )

    category = state.get("dispute_category") or dispute.dispute_category
    if category in PAYMENT_DISPUTE_CATEGORIES:
        logger.info(
            "[Interrupt raised] waiting_approval_node (settlement confirmation)"
        )
        raise NodeInterrupt(
            "Workflow paused: Waiting for associate to confirm settlement."
        )

    logger.info("[Interrupt raised] waiting_approval_node")
    raise NodeInterrupt("Workflow paused: Waiting for Associate Approval decision.")


async def waiting_resolution_node(
    state: DisputeWorkflowState, config: RunnableConfig
) -> dict[str, Any]:
    """Pauses workflow in WAITING_PAYMENT_REVIEW or WAITING_INTERNAL_TEAM status or handles resumption."""
    logger.info("[Node Start] waiting_resolution_node")
    db = config["configurable"]["db"]
    dispute_id = state["dispute_id"]
    category = state.get("dispute_category")

    outcome = state.get("resolution_outcome")

    dispute_repo = DisputeRepository(db)
    dispute = await dispute_repo.get_by_id(dispute_id)
    if not dispute:
        return {}

    activity_repo = ActivityRepository(db)
    audit_service = AuditService(activity_repo)

    if outcome in [
        "SETTLEMENT_DONE",
        "SETTLEMENT_NOT_DONE",
        "ACKNOWLEDGED",
        "REJECTED",
    ]:
        final_outcome = (
            "CUSTOMER_CORRECT"
            if outcome in ["SETTLEMENT_DONE", "ACKNOWLEDGED"]
            else "COMPANY_CORRECT"
        )

        old_status = dispute.status
        dispute.status = "RESOLVED"
        dispute.resolution_outcome = final_outcome
        await dispute_repo.update_dispute(dispute)

        await audit_service.log_event(
            dispute_id=dispute_id,
            action="STATUS_CHANGED",
            metadata={
                "old_status": old_status,
                "new_status": "RESOLVED",
                "outcome": final_outcome,
            },
        )
        return {
            "resolution_outcome": final_outcome,
            "workflow_status": "RESOLVED",
            "current_node": "waiting_resolution_node",
        }

    # Route sub-status
    wait_status = "WAITING_INTERNAL_TEAM"
    if category in ["PAYMENT_ALREADY_DONE", "PAYMENT_NOT_REFLECTED"]:
        wait_status = "WAITING_PAYMENT_REVIEW"

    # Update dispute status
    old_status = dispute.status
    dispute.status = wait_status
    await dispute_repo.update_dispute(dispute)

    # Log pause activity
    await audit_service.log_event(
        dispute_id=dispute_id,
        action="STATUS_CHANGED",
        metadata={"old_status": old_status, "new_status": wait_status},
    )

    logger.info("[Interrupt raised] waiting_resolution_node (%s)", wait_status)
    raise NodeInterrupt(f"Workflow paused: {wait_status}")


# --- ROUTERS ---


def route_after_intake(state: DisputeWorkflowState) -> str:
    """Routes after case intake node."""
    if state.get("metadata", {}).get("is_idempotent_bypass"):
        return END
    return "triage_agent_node"


def route_after_triage(state: DisputeWorkflowState) -> str:
    """Routes after triage agent step."""
    if state.get("dispute_id"):
        # Bypass for dispute workflow execution
        return "correlation_node"
    return "pre_correlation_node"


def route_after_pre_correlation(state: DisputeWorkflowState) -> str:
    """Routes after pre-correlation: resume existing dispute or generate new ones."""
    if state.get("workflow_status") == "CORRELATED_EXISTING":
        return END
    return "dispute_generation_node"


def route_after_correlation(state: DisputeWorkflowState) -> str:
    """Stops duplicate disputes from continuing through resolution nodes."""
    if state.get("workflow_status") == "CANCELLED_DUPLICATE":
        return END
    return "assignment_node"


def route_after_validation(state: DisputeWorkflowState) -> str:
    """Routes validation check outcomes."""
    if state.get("requires_human_review"):
        return "review_queue_node"
    return "routing_node"


def route_collections_destination(state: DisputeWorkflowState) -> str:
    """Routes to path starting nodes based on category after pausing collections."""
    category = state.get("dispute_category")
    if category == "AMENDMENT":
        return "invoice_fetcher_node"
    elif category in ["PAYMENT_ALREADY_DONE", "PAYMENT_NOT_REFLECTED"]:
        return "reference_extraction_node"
    else:  # QUALITY, LATE_DELIVERY, OTHER, DUPLICATE_INVOICE etc.
        return "department_contact_node"


def route_after_waiting_approval(state: DisputeWorkflowState) -> str:
    """Routes after associate approval: apply amendment or proceed to mail."""
    category = state.get("dispute_category")
    outcome = state.get("resolution_outcome")
    approved_amendment = outcome in (
        "CUSTOMER_CORRECT",
        "APPROVE",
        "EDIT_AND_APPLY",
    )
    if category == "AMENDMENT" and approved_amendment:
        return "apply_amendment_node"
    return "mail_agent_node"


def route_after_amendment_resolution(state: DisputeWorkflowState) -> str:
    """Routes after amendment resolution agent."""
    outcome = state.get("resolution_outcome")
    if outcome == "CUSTOMER_CORRECT":
        return "waiting_approval_node"
    elif outcome == "COMPANY_CORRECT":
        return "mail_agent_node"
    return END


def route_after_payment_checker(state: DisputeWorkflowState) -> str:
    """Routes after payment checker node."""
    outcome = state.get("resolution_outcome")
    if outcome == "CUSTOMER_CORRECT":
        return "waiting_approval_node"
    if outcome == "COMPANY_CORRECT":
        return "mail_agent_node"
    if outcome == "PENDING_INTERNAL_REVIEW":
        return "waiting_resolution_node"
    return END


def route_after_department_contact(state: DisputeWorkflowState) -> str:
    """Routes after department contact node."""
    return "waiting_resolution_node"


def cls_determine_route(category: str | None) -> str:
    """Determines route category."""
    if not category:
        return "OTHER"
    cat = category.upper().strip()
    if cat in [
        "AMENDMENT",
        "PAYMENT_ALREADY_DONE",
        "PAYMENT_NOT_REFLECTED",
        "DUPLICATE_INVOICE",
        "QUALITY",
        "LATE_DELIVERY",
        "OTHER",
    ]:
        return cat
    return "OTHER"


# --- BUILD STATE GRAPH ---


def create_graph() -> StateGraph:
    """Builds the unified dispute lifecycle workflow graph."""
    workflow = StateGraph(DisputeWorkflowState)

    # Nodes
    workflow.add_node("case_intake_node", case_intake_node)
    workflow.add_node("triage_agent_node", triage_agent_node)
    workflow.add_node("pre_correlation_node", pre_correlation_node)
    workflow.add_node("dispute_generation_node", dispute_generation_node)
    workflow.add_node("correlation_node", correlation_node)
    workflow.add_node("assignment_node", assignment_node)
    workflow.add_node("validation_node", validation_node)
    workflow.add_node("review_queue_node", review_queue_node)
    workflow.add_node("routing_node", routing_node)
    workflow.add_node("collections_node", collections_node)
    workflow.add_node("invoice_fetcher_node", invoice_fetcher_node)
    workflow.add_node("amendment_resolution_node", amendment_resolution_node)
    workflow.add_node("reference_extraction_node", reference_extraction_node)
    workflow.add_node("payment_checker_node", payment_checker_node)
    workflow.add_node("department_contact_node", department_contact_node)
    workflow.add_node("waiting_approval_node", waiting_approval_node)
    workflow.add_node("apply_amendment_node", apply_amendment_node)
    workflow.add_node("waiting_resolution_node", waiting_resolution_node)
    workflow.add_node("mail_agent_node", mail_agent_node)
    workflow.add_node("close_dispute_node", close_dispute_node)

    # Entry point
    workflow.set_entry_point("case_intake_node")

    # Edges
    workflow.add_conditional_edges(
        "case_intake_node",
        route_after_intake,
        {
            "triage_agent_node": "triage_agent_node",
            END: END,
        },
    )
    workflow.add_conditional_edges(
        "triage_agent_node",
        route_after_triage,
        {
            "pre_correlation_node": "pre_correlation_node",
            "correlation_node": "correlation_node",
        },
    )
    workflow.add_conditional_edges(
        "pre_correlation_node",
        route_after_pre_correlation,
        {
            "dispute_generation_node": "dispute_generation_node",
            END: END,
        },
    )
    workflow.add_edge("dispute_generation_node", END)

    workflow.add_conditional_edges(
        "correlation_node",
        route_after_correlation,
        {
            "assignment_node": "assignment_node",
            END: END,
        },
    )
    workflow.add_edge("assignment_node", "validation_node")

    workflow.add_conditional_edges(
        "validation_node",
        route_after_validation,
        {
            "review_queue_node": "review_queue_node",
            "routing_node": "routing_node",
        },
    )

    # After review resolution, return back to Validation Node
    workflow.add_edge("review_queue_node", "validation_node")

    workflow.add_edge("routing_node", "collections_node")

    workflow.add_conditional_edges(
        "collections_node",
        route_collections_destination,
        {
            "invoice_fetcher_node": "invoice_fetcher_node",
            "reference_extraction_node": "reference_extraction_node",
            "department_contact_node": "department_contact_node",
        },
    )

    # Amendment Path
    workflow.add_edge("invoice_fetcher_node", "amendment_resolution_node")
    workflow.add_conditional_edges(
        "amendment_resolution_node",
        route_after_amendment_resolution,
        {
            "waiting_approval_node": "waiting_approval_node",
            "mail_agent_node": "mail_agent_node",
            END: END,
        },
    )
    workflow.add_conditional_edges(
        "waiting_approval_node",
        route_after_waiting_approval,
        {
            "apply_amendment_node": "apply_amendment_node",
            "mail_agent_node": "mail_agent_node",
        },
    )
    workflow.add_edge("apply_amendment_node", "mail_agent_node")

    # Payment Path
    workflow.add_edge("reference_extraction_node", "payment_checker_node")
    workflow.add_conditional_edges(
        "payment_checker_node",
        route_after_payment_checker,
        {
            "mail_agent_node": "mail_agent_node",
            "waiting_resolution_node": "waiting_resolution_node",
            END: END,
        },
    )

    # Operational Path
    workflow.add_conditional_edges(
        "department_contact_node",
        route_after_department_contact,
        {
            "waiting_resolution_node": "waiting_resolution_node",
        },
    )

    # Resumed waits move to Mail
    workflow.add_edge("waiting_resolution_node", "mail_agent_node")

    # Final steps
    workflow.add_edge("mail_agent_node", "close_dispute_node")
    workflow.add_edge("close_dispute_node", END)

    # Custom PostgreSQL checkpointer
    checkpointer = DbWorkflowCheckpointer()
    return workflow.compile(checkpointer=checkpointer)


_compiled_graph = None


def get_graph():
    """Retrieves or initializes the compiled StateGraph instance."""
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = create_graph()
    return _compiled_graph
