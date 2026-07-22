from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config.settings import settings
from src.core.exceptions.business_exceptions import ValidationException
from src.core.services.assignment_service import AssignmentService
from src.core.services.audit_service import AuditService
from src.core.services.correlation_service import CorrelationService
from src.core.services.escalation_service import EscalationService
from src.core.services.sla_service import (
    PAUSE_REASON_AWAITING_CUSTOMER_REPLY,
    PAUSE_REASON_WAITING_CUSTOMER,
    SLA_MONITORING_STATUSES,
    SLAService,
)
from src.core.workflow.triage_agent import DisputeTriageAgent
from src.data.repositories import (
    ActivityRepository,
    AssignmentRepository,
    CaseRepository,
    CommentRepository,
    CommunicationRepository,
    DisputeRepository,
    EscalationRepository,
    SLARepository,
    UserRepository,
)


@pytest.mark.asyncio
async def test_sla_durations_configuration(db_session: AsyncSession):
    case_repo = CaseRepository(db_session)
    dispute_repo = DisputeRepository(db_session)
    sla_repo = SLARepository(db_session)
    activity_repo = ActivityRepository(db_session)

    audit_service = AuditService(activity_repo)
    sla_service = SLAService(sla_repo, dispute_repo, audit_service, settings)

    case = await case_repo.create_case(
        case_number=f"CASE-{uuid4().hex[:6].upper()}",
        customer_email="customer@example.com",
    )

    # Categories to test and their expected minutes
    test_cases = [
        ("PAYMENT_ALREADY_DONE", 12 * 60),
        ("PAYMENT_NOT_REFLECTED", 12 * 60),
        ("AMENDMENT", 24 * 60),
        ("DUPLICATE_INVOICE", 24 * 60),
        ("QUALITY", 72 * 60),
        ("LATE_DELIVERY", 72 * 60),
        ("OTHER", 72 * 60),
    ]

    for category, expected_minutes in test_cases:
        dispute = await dispute_repo.create_dispute(
            dispute_number=f"DISP-{uuid4().hex[:6].upper()}",
            case_id=case.id,
            invoice_id=uuid4(),
            invoice_number=f"INV-{uuid4().hex[:4].upper()}",
            customer_id=uuid4(),
            dispute_category=category,
        )
        sla = await sla_service.create_sla(dispute.id)
        assert sla.sla_minutes == expected_minutes
        assert sla.status == "ON_TRACK"


@pytest.mark.asyncio
async def test_sla_pause_and_resume(db_session: AsyncSession):
    case_repo = CaseRepository(db_session)
    dispute_repo = DisputeRepository(db_session)
    sla_repo = SLARepository(db_session)
    activity_repo = ActivityRepository(db_session)

    audit_service = AuditService(activity_repo)
    sla_service = SLAService(sla_repo, dispute_repo, audit_service, settings)

    case = await case_repo.create_case(
        case_number=f"CASE-{uuid4().hex[:6].upper()}",
        customer_email="customer@example.com",
    )
    dispute = await dispute_repo.create_dispute(
        dispute_number=f"DISP-{uuid4().hex[:6].upper()}",
        case_id=case.id,
        invoice_id=uuid4(),
        invoice_number="INV-1001",
        customer_id=uuid4(),
        dispute_category="PAYMENT_ALREADY_DONE",
        status="OPEN",
    )

    # 1. Create SLA
    sla = await sla_service.create_sla(dispute.id)
    assert sla.is_paused is False

    # 2. Transition to WAITING_CUSTOMER (Pause)
    await sla_service.handle_status_change(dispute.id, "OPEN", "WAITING_CUSTOMER")
    await db_session.refresh(sla)
    assert sla.is_paused is True
    assert sla.pause_reason == PAUSE_REASON_WAITING_CUSTOMER
    assert sla.paused_at is not None

    # Simulate passing of time in pause (mock paused_at to be 30 minutes ago)
    sla.paused_at = datetime.now(UTC) - timedelta(minutes=30)
    await sla_repo.update_sla(sla)

    # 3. Transition back to IN_REVIEW (Resume)
    await sla_service.handle_status_change(dispute.id, "WAITING_CUSTOMER", "IN_REVIEW")
    await db_session.refresh(sla)
    assert sla.is_paused is False
    assert sla.pause_reason is None
    assert sla.paused_at is None
    assert sla.accumulated_paused_minutes >= 30.0


@pytest.mark.asyncio
async def test_sla_closed_when_dispute_closed(db_session: AsyncSession):
    case_repo = CaseRepository(db_session)
    dispute_repo = DisputeRepository(db_session)
    sla_repo = SLARepository(db_session)
    activity_repo = ActivityRepository(db_session)

    audit_service = AuditService(activity_repo)
    sla_service = SLAService(sla_repo, dispute_repo, audit_service, settings)

    case = await case_repo.create_case(
        case_number=f"CASE-{uuid4().hex[:6].upper()}",
        customer_email="customer@example.com",
    )
    dispute = await dispute_repo.create_dispute(
        dispute_number=f"DISP-{uuid4().hex[:6].upper()}",
        case_id=case.id,
        invoice_id=uuid4(),
        invoice_number="INV-9001",
        customer_id=uuid4(),
        dispute_category="AMENDMENT",
        status="OPEN",
    )

    sla = await sla_service.create_sla(dispute.id)
    dispute.status = "CLOSED"
    await dispute_repo.update_dispute(dispute)

    await sla_service.handle_status_change(dispute.id, "OPEN", "CLOSED")
    await db_session.refresh(sla)
    assert sla.is_paused is False
    assert sla.status == "CLOSED"

    updated = await sla_service.calculate_progress(dispute.id)
    assert updated is not None
    assert updated.status == "CLOSED"


@pytest.mark.asyncio
async def test_sla_closed_when_dispute_failed(db_session: AsyncSession):
    case_repo = CaseRepository(db_session)
    dispute_repo = DisputeRepository(db_session)
    sla_repo = SLARepository(db_session)
    activity_repo = ActivityRepository(db_session)

    audit_service = AuditService(activity_repo)
    sla_service = SLAService(sla_repo, dispute_repo, audit_service, settings)

    case = await case_repo.create_case(
        case_number=f"CASE-{uuid4().hex[:6].upper()}",
        customer_email="customer@example.com",
    )
    dispute = await dispute_repo.create_dispute(
        dispute_number=f"DISP-{uuid4().hex[:6].upper()}",
        case_id=case.id,
        invoice_id=uuid4(),
        invoice_number="INV-9002",
        customer_id=uuid4(),
        dispute_category="PAYMENT_ALREADY_DONE",
        status="OPEN",
    )

    sla = await sla_service.create_sla(dispute.id)
    dispute.status = "FAILED"
    await dispute_repo.update_dispute(dispute)

    await sla_service.handle_status_change(dispute.id, "OPEN", "FAILED")
    await db_session.refresh(sla)
    assert sla.is_paused is False
    assert sla.status == "CLOSED"

    updated = await sla_service.calculate_progress(dispute.id)
    assert updated is not None
    assert updated.status == "CLOSED"


@pytest.mark.asyncio
async def test_escalation_rules(db_session: AsyncSession, seed_users):
    case_repo = CaseRepository(db_session)
    dispute_repo = DisputeRepository(db_session)
    sla_repo = SLARepository(db_session)
    activity_repo = ActivityRepository(db_session)
    escalation_repo = EscalationRepository(db_session)

    audit_service = AuditService(activity_repo)
    sla_service = SLAService(sla_repo, dispute_repo, audit_service, settings)
    escalation_service = EscalationService(
        escalation_repo, sla_repo, dispute_repo, audit_service
    )

    case = await case_repo.create_case(
        case_number=f"CASE-{uuid4().hex[:6].upper()}",
        customer_email="customer@example.com",
    )

    # Create dispute assigned to seeded associate
    associate = seed_users["associate"]
    manager = seed_users["manager"]

    dispute = await dispute_repo.create_dispute(
        dispute_number=f"DISP-{uuid4().hex[:6].upper()}",
        case_id=case.id,
        invoice_id=uuid4(),
        invoice_number="INV-1002",
        customer_id=uuid4(),
        dispute_category="AMENDMENT",
        status="OPEN",
        assigned_to=associate.id,
        manager_id=manager.id,
    )

    sla = await sla_service.create_sla(dispute.id)

    # Mock started_at to simulate 85% elapsed time (24 hours SLA = 1440 mins. 85% = 1224 mins)
    sla.started_at = datetime.now(UTC) - timedelta(minutes=1224)
    await sla_repo.update_sla(sla)

    # Calculate progress and run escalations
    await sla_service.calculate_progress(dispute.id)
    await escalation_service.check_and_trigger_escalations(dispute.id)

    # Should trigger L1 (AT_RISK)
    escalations = await escalation_repo.get_escalations_for_dispute(dispute.id)
    assert len(escalations) == 1
    assert escalations[0].level == 1
    assert escalations[0].escalated_to == associate.id

    # Mock started_at to simulate 105% elapsed time (1512 mins)
    sla.started_at = datetime.now(UTC) - timedelta(minutes=1512)
    await sla_repo.update_sla(sla)

    # Recalculate
    await sla_service.calculate_progress(dispute.id)
    await escalation_service.check_and_trigger_escalations(dispute.id)

    # Should trigger L2 (BREACHED)
    escalations = await escalation_repo.get_escalations_for_dispute(dispute.id)
    assert len(escalations) == 2
    levels = {e.level for e in escalations}
    assert 2 in levels

    # Verify status changed to ESCALATED
    await db_session.refresh(dispute)
    assert dispute.status == "ESCALATED"


@pytest.mark.asyncio
async def test_manual_escalation(db_session: AsyncSession, seed_users):
    case_repo = CaseRepository(db_session)
    dispute_repo = DisputeRepository(db_session)
    escalation_repo = EscalationRepository(db_session)
    activity_repo = ActivityRepository(db_session)
    sla_repo = SLARepository(db_session)
    audit_service = AuditService(activity_repo)
    escalation_service = EscalationService(
        escalation_repo, sla_repo, dispute_repo, audit_service
    )

    case = await case_repo.create_case(
        case_number=f"CASE-{uuid4().hex[:6].upper()}",
        customer_email="customer@example.com",
    )

    associate = seed_users["associate"]
    manager = seed_users["manager"]

    dispute = await dispute_repo.create_dispute(
        dispute_number=f"DISP-{uuid4().hex[:6].upper()}",
        case_id=case.id,
        invoice_id=uuid4(),
        invoice_number="INV-1003",
        customer_id=uuid4(),
        dispute_category="AMENDMENT",
        status="OPEN",
        assigned_to=associate.id,
        manager_id=manager.id,
    )

    # Escalates manually
    reason_notes = "Manual escalation request due to override"
    await escalation_service.escalate_to_manager_manually(
        dispute_id=dispute.id,
        reason=reason_notes,
    )

    # Verify status changed to ESCALATED
    await db_session.refresh(dispute)
    assert dispute.status == "ESCALATED"

    # Verify Level 2 escalation record created
    escalations = await escalation_repo.get_escalations_for_dispute(dispute.id)
    assert len(escalations) == 1
    assert escalations[0].level == 2
    assert escalations[0].escalated_to == manager.id
    assert escalations[0].reason == reason_notes

    # Verify internal comment was recorded
    comments = await CommentRepository(db_session).list_comments_for_dispute(dispute.id)
    assert len(comments) == 1
    assert "Dispute escalated to manager" in comments[0].comment
    assert reason_notes in comments[0].comment


@pytest.mark.asyncio
async def test_correlation_engine(db_session: AsyncSession):
    case_repo = CaseRepository(db_session)
    dispute_repo = DisputeRepository(db_session)
    comm_repo = CommunicationRepository(db_session)
    comment_repo = CommentRepository(db_session)
    activity_repo = ActivityRepository(db_session)

    audit_service = AuditService(activity_repo)
    correlation_service = CorrelationService(
        dispute_repo, comm_repo, comment_repo, audit_service
    )

    case = await case_repo.create_case(
        case_number=f"CASE-{uuid4().hex[:6].upper()}",
        customer_email="customer@example.com",
    )

    # Create active dispute for INV-5000 with PRICING (which collapses to AMENDMENT)
    dispute = await dispute_repo.create_dispute(
        dispute_number=f"DISP-{uuid4().hex[:6].upper()}",
        case_id=case.id,
        invoice_id=uuid4(),
        invoice_number="INV-5000",
        customer_id=uuid4(),
        dispute_category="AMENDMENT",
        status="OPEN",
    )

    # Match check on TAX (collapses to AMENDMENT)
    correlated = await correlation_service.correlate_dispute(
        invoice_number="INV-5000",
        raw_category="TAX",
        customer_email="customer@example.com",
        email_subject="Tax Dispute",
        email_body="Tax is wrong.",
    )

    assert correlated is not None
    assert correlated.dispute_id == dispute.id

    # Verify communication was attached
    comms = await comm_repo.get_communications_for_dispute(dispute.id)
    assert len(comms) == 1
    assert comms[0].subject == "Tax Dispute"


@pytest.mark.asyncio
async def test_correlation_waiting_customer_reply_across_payment_categories(
    db_session: AsyncSession,
):
    """Customer replies to a WAITING_CUSTOMER dispute even if triage category differs."""
    case_repo = CaseRepository(db_session)
    dispute_repo = DisputeRepository(db_session)
    comm_repo = CommunicationRepository(db_session)
    comment_repo = CommentRepository(db_session)
    activity_repo = ActivityRepository(db_session)

    audit_service = AuditService(activity_repo)
    correlation_service = CorrelationService(
        dispute_repo, comm_repo, comment_repo, audit_service
    )

    case = await case_repo.create_case(
        case_number=f"CASE-{uuid4().hex[:6].upper()}",
        customer_email="customer@example.com",
    )
    dispute = await dispute_repo.create_dispute(
        dispute_number=f"DISP-{uuid4().hex[:6].upper()}",
        case_id=case.id,
        invoice_id=uuid4(),
        invoice_number="INV-2599",
        customer_id=uuid4(),
        dispute_category="PAYMENT_ALREADY_DONE",
        status="WAITING_CUSTOMER",
    )

    correlated = await correlation_service.correlate_dispute(
        invoice_number="2599",
        raw_category="PAYMENT_NOT_REFLECTED",
        customer_email="customer@example.com",
        email_subject="Re: UTR details",
        email_body="UTR for invoice 2599 is HDFCNEFT2026070251782. the payment is not reflected",
    )

    assert correlated is not None
    assert correlated.dispute_id == dispute.id

    comments = await comment_repo.list_comments_for_dispute(dispute.id)
    customer_comments = [
        comment for comment in comments if comment.comment_type == "CUSTOMER_COMMENT"
    ]
    assert len(customer_comments) == 1
    assert "HDFCNEFT2026070251782" in customer_comments[0].comment


@pytest.mark.asyncio
async def test_correlation_prefers_payment_dispute_over_amendment_for_utr_email(
    db_session: AsyncSession,
):
    """UTR / payment-not-reflected email should attach to the payment dispute, not amendment."""
    case_repo = CaseRepository(db_session)
    dispute_repo = DisputeRepository(db_session)
    comm_repo = CommunicationRepository(db_session)
    comment_repo = CommentRepository(db_session)
    activity_repo = ActivityRepository(db_session)

    audit_service = AuditService(activity_repo)
    correlation_service = CorrelationService(
        dispute_repo, comm_repo, comment_repo, audit_service
    )

    case = await case_repo.create_case(
        case_number=f"CASE-{uuid4().hex[:6].upper()}",
        customer_email="customer@example.com",
    )
    amendment_dispute = await dispute_repo.create_dispute(
        dispute_number=f"DISP-{uuid4().hex[:6].upper()}",
        case_id=case.id,
        invoice_id=uuid4(),
        invoice_number="INV-2599",
        customer_id=uuid4(),
        dispute_category="AMENDMENT",
        status="WAITING_CUSTOMER",
    )
    payment_dispute = await dispute_repo.create_dispute(
        dispute_number=f"DISP-{uuid4().hex[:6].upper()}",
        case_id=case.id,
        invoice_id=uuid4(),
        invoice_number="INV-2599",
        customer_id=uuid4(),
        dispute_category="PAYMENT_NOT_REFLECTED",
        status="OPEN",
    )

    correlated = await correlation_service.find_correlated_dispute_for_intake(
        invoices=[
            {
                "invoice_number": "INV-2599",
                "dispute_types": ["PAYMENT_NOT_REFLECTED"],
            }
        ],
        customer_email="customer@example.com",
        email_subject="Dispute",
        email_body=(
            "the utr for the invoice 2599 is HDFCNEFT2026070251782."
            "the payment is not reflected"
        ),
    )

    assert correlated is not None
    assert correlated.dispute_id == payment_dispute.id
    assert correlated.dispute_id != amendment_dispute.id


@pytest.mark.asyncio
async def test_correlation_intake_by_customer_email_waiting_customer(
    db_session: AsyncSession,
):
    """Customer reply without invoice number still resumes WAITING_CUSTOMER dispute."""
    case_repo = CaseRepository(db_session)
    dispute_repo = DisputeRepository(db_session)
    comm_repo = CommunicationRepository(db_session)
    comment_repo = CommentRepository(db_session)
    activity_repo = ActivityRepository(db_session)

    audit_service = AuditService(activity_repo)
    correlation_service = CorrelationService(
        dispute_repo, comm_repo, comment_repo, audit_service
    )

    case = await case_repo.create_case(
        case_number=f"CASE-{uuid4().hex[:6].upper()}",
        customer_email="customer@example.com",
    )
    dispute = await dispute_repo.create_dispute(
        dispute_number=f"DISP-{uuid4().hex[:6].upper()}",
        case_id=case.id,
        invoice_id=uuid4(),
        invoice_number="INV-3001",
        customer_id=uuid4(),
        dispute_category="PAYMENT_ALREADY_DONE",
        status="WAITING_CUSTOMER",
    )

    correlated = await correlation_service.find_correlated_dispute_for_intake(
        invoices=[],
        customer_email="customer@example.com",
        email_subject="Re: Additional payment details",
        email_body="Here is the UTR: HDFCNEFT2026070251782",
    )

    assert correlated is not None
    assert correlated.dispute_id == dispute.id
    await db_session.refresh(dispute)
    assert dispute.status == "OPEN"


@pytest.mark.asyncio
async def test_correlation_intake_by_gmail_thread_without_invoice_number(
    db_session: AsyncSession,
):
    """Reply in the same Gmail thread resumes the dispute without invoice/dispute references."""
    case_repo = CaseRepository(db_session)
    dispute_repo = DisputeRepository(db_session)
    comm_repo = CommunicationRepository(db_session)
    comment_repo = CommentRepository(db_session)
    activity_repo = ActivityRepository(db_session)

    audit_service = AuditService(activity_repo)
    correlation_service = CorrelationService(
        dispute_repo,
        comm_repo,
        comment_repo,
        audit_service,
        case_repo,
    )

    case = await case_repo.create_case(
        case_number=f"CASE-{uuid4().hex[:6].upper()}",
        customer_email="customer@example.com",
        gmail_thread_id="gmail-thread-001",
        rfc_message_id="<original-msg@gmail.com>",
    )
    dispute = await dispute_repo.create_dispute(
        dispute_number=f"DISP-{uuid4().hex[:6].upper()}",
        case_id=case.id,
        invoice_id=uuid4(),
        invoice_number="INV-4001",
        customer_id=uuid4(),
        dispute_category="AMENDMENT",
        status="WAITING_CUSTOMER",
    )
    await db_session.commit()

    correlated = await correlation_service.find_correlated_dispute_for_intake(
        invoices=[],
        customer_email="customer@example.com",
        email_subject="Re: Additional documents",
        email_body="Please find the requested purchase order attached.",
        gmail_thread_id="gmail-thread-001",
        in_reply_to="<original-msg@gmail.com>",
        email_references="<original-msg@gmail.com>",
    )

    assert correlated is not None
    assert correlated.dispute_id == dispute.id
    await db_session.refresh(dispute)
    assert dispute.status == "OPEN"


@pytest.mark.asyncio
async def test_correlation_intake_does_not_attach_new_invoice_to_unrelated_waiting_dispute(
    db_session: AsyncSession,
):
    """A new invoice/issue in the same thread must create a dispute, not resume another invoice."""
    case_repo = CaseRepository(db_session)
    dispute_repo = DisputeRepository(db_session)
    comm_repo = CommunicationRepository(db_session)
    comment_repo = CommentRepository(db_session)
    activity_repo = ActivityRepository(db_session)

    audit_service = AuditService(activity_repo)
    correlation_service = CorrelationService(
        dispute_repo,
        comm_repo,
        comment_repo,
        audit_service,
        case_repo,
    )

    customer_email = "starkindustriestony0@gmail.com"
    case = await case_repo.create_case(
        case_number=f"CASE-{uuid4().hex[:6].upper()}",
        customer_email=customer_email,
        gmail_thread_id="gmail-thread-late-delivery",
        email_subject="Wrong due date",
        email_body="Due date on invoice 4502 is wrong.",
    )
    await dispute_repo.create_dispute(
        dispute_number=f"DISP-{uuid4().hex[:6].upper()}",
        case_id=case.id,
        invoice_id=uuid4(),
        invoice_number="INV-4502",
        customer_id=uuid4(),
        dispute_category="AMENDMENT",
        status="WAITING_CUSTOMER",
    )
    await db_session.commit()

    correlated = await correlation_service.find_correlated_dispute_for_intake(
        invoices=[
            {
                "invoice_number": "INV-4503",
                "dispute_types": ["LATE_DELIVERY"],
            }
        ],
        customer_email=customer_email,
        email_subject="late delivery",
        email_body="Late deivery for invoice 4503",
        gmail_thread_id="gmail-thread-late-delivery",
    )

    assert correlated is None


@pytest.mark.asyncio
async def test_pre_correlation_node_skips_dispute_generation(
    db_session: AsyncSession,
):
    """Pre-correlation resumes an existing dispute instead of creating a new one."""
    from unittest.mock import patch

    from src.core.workflow.graph import get_graph, pre_correlation_node

    case_repo = CaseRepository(db_session)
    dispute_repo = DisputeRepository(db_session)

    case = await case_repo.create_case(
        case_number=f"CASE-{uuid4().hex[:6].upper()}",
        customer_email="customer@example.com",
        email_subject="Re: UTR details",
        email_body="UTR is HDFCNEFT2026070251782",
    )
    dispute = await dispute_repo.create_dispute(
        dispute_number=f"DISP-{uuid4().hex[:6].upper()}",
        case_id=case.id,
        invoice_id=uuid4(),
        invoice_number="INV-2599",
        customer_id=uuid4(),
        dispute_category="PAYMENT_ALREADY_DONE",
        status="WAITING_CUSTOMER",
    )
    await db_session.commit()

    config = {
        "configurable": {
            "db": db_session,
            "thread_id": str(case.id),
        }
    }
    state = {
        "customer_email": case.customer_email,
        "email_subject": case.email_subject,
        "email_body": case.email_body,
        "raw_content": case.email_body,
        "invoices": [],
        "metadata": {},
    }

    result = await pre_correlation_node(state, config)
    assert result["workflow_status"] == "CORRELATED_EXISTING"
    assert result["metadata"]["resume_dispute_id"] == str(dispute.id)

    disputes_before = len(await dispute_repo.list_disputes())
    triage_mock_val = {"invoices": [], "confidence": 95.0}

    from unittest.mock import AsyncMock

    with (
        patch(
            "src.core.workflow.triage_agent.DisputeTriageAgent.triage_communication",
            new_callable=AsyncMock,
            return_value=triage_mock_val,
        ),
        patch(
            "src.infrastructure.celery.tasks.process_dispute_workflow.delay"
        ) as mock_workflow_delay,
    ):
        graph = get_graph()
        await graph.ainvoke(
            {
                "customer_email": case.customer_email,
                "email_subject": "Re: payment info",
                "email_body": "UTR is HDFCNEFT2026070251782",
                "raw_content": "UTR is HDFCNEFT2026070251782",
                "message_id": f"msg-{uuid4().hex}",
                "workflow_status": "START",
                "current_node": "START",
                "requires_human_review": False,
                "errors": [],
                "metadata": {},
            },
            config,
        )

    disputes_after = len(await dispute_repo.list_disputes())
    assert disputes_after == disputes_before
    mock_workflow_delay.assert_not_called()


@pytest.mark.asyncio
async def test_conversation_history_includes_original_and_follow_up(
    db_session: AsyncSession,
):
    """Amendment agent context should include the full customer thread."""
    from src.core.services.conversation_history_service import (
        ConversationHistoryService,
    )

    case_repo = CaseRepository(db_session)
    dispute_repo = DisputeRepository(db_session)
    comm_repo = CommunicationRepository(db_session)
    comment_repo = CommentRepository(db_session)

    case = await case_repo.create_case(
        case_number=f"CASE-{uuid4().hex[:6].upper()}",
        customer_email="customer@example.com",
        email_subject="Tax issue on invoice 2799",
        email_body="invoice 2799 has wrong tax",
        raw_content="invoice 2799 has wrong tax",
    )
    dispute = await dispute_repo.create_dispute(
        dispute_number=f"DISP-{uuid4().hex[:6].upper()}",
        case_id=case.id,
        invoice_id=uuid4(),
        invoice_number="INV-2799",
        customer_id=uuid4(),
        dispute_category="AMENDMENT",
        status="WAITING_CUSTOMER",
    )
    await comm_repo.create_communication(
        dispute_id=dispute.id,
        recipient="customer@example.com",
        subject="Re: Tax issue",
        body="this is the po of the invoice 2799",
        communication_type="CUSTOMER",
    )
    await comment_repo.create_comment(
        dispute_id=dispute.id,
        comment="this is the po of the invoice 2799",
        comment_type="CUSTOMER_COMMENT",
        created_by=uuid4(),
    )
    await db_session.commit()

    dispute = await dispute_repo.get_by_id(dispute.id)
    history_service = ConversationHistoryService(comm_repo, comment_repo)
    history = await history_service.build_customer_conversation_text(
        dispute.id,
        dispute,
    )

    assert "wrong tax" in history
    assert "po of the invoice 2799" in history
    assert history.index("wrong tax") < history.index("po of the invoice 2799")


def test_triage_regex_extracts_bare_invoice_number():
    """Regex fallback should understand 'invoice 2599' style references."""
    result = DisputeTriageAgent._regex_fallback(
        subject="Re: payment",
        body="UTR for invoice 2599 is HDFCNEFT2026070251782. the payment is not reflected",
        raw_content=None,
    )
    invoice_numbers = [inv["invoice_number"] for inv in result["invoices"]]
    assert "INV-2599" in invoice_numbers


@pytest.mark.asyncio
async def test_assignment_service(db_session: AsyncSession, seed_users):
    dispute_repo = DisputeRepository(db_session)
    case_repo = CaseRepository(db_session)
    assign_repo = AssignmentRepository(db_session)
    activity_repo = ActivityRepository(db_session)
    user_repo = UserRepository(db_session)

    audit_service = AuditService(activity_repo)
    assign_service = AssignmentService(
        dispute_repo, assign_repo, user_repo, audit_service
    )

    case = await case_repo.create_case(
        case_number=f"CASE-{uuid4().hex[:6].upper()}",
        customer_email="customer@example.com",
    )
    dispute = await dispute_repo.create_dispute(
        dispute_number=f"DISP-{uuid4().hex[:6].upper()}",
        case_id=case.id,
        invoice_id=uuid4(),
        invoice_number="INV-1003",
        customer_id=uuid4(),
        dispute_category="AMENDMENT",
        status="OPEN",
    )

    # Trigger automatic assignment
    updated_dispute = await assign_service.assign_dispute(dispute.id)
    assert updated_dispute.assigned_to == seed_users["associate"].id
    assert updated_dispute.manager_id == seed_users["manager"].id

    # Verify assignment history
    active_assign = await assign_repo.get_active_assignment_for_dispute(dispute.id)
    assert active_assign is not None
    assert active_assign.assigned_to == seed_users["associate"].id


@pytest.mark.asyncio
async def test_sla_not_paused_for_waiting_internal_team(db_session: AsyncSession):
    """SLA keeps running for internal waits; only WAITING_CUSTOMER pauses the clock."""
    case_repo = CaseRepository(db_session)
    dispute_repo = DisputeRepository(db_session)
    sla_repo = SLARepository(db_session)
    activity_repo = ActivityRepository(db_session)

    audit_service = AuditService(activity_repo)
    sla_service = SLAService(sla_repo, dispute_repo, audit_service, settings)

    case = await case_repo.create_case(
        case_number=f"CASE-{uuid4().hex[:6].upper()}",
        customer_email="customer@example.com",
    )
    dispute = await dispute_repo.create_dispute(
        dispute_number=f"DISP-{uuid4().hex[:6].upper()}",
        case_id=case.id,
        invoice_id=uuid4(),
        invoice_number="INV-3001",
        customer_id=uuid4(),
        dispute_category="QUALITY",
        status="OPEN",
    )

    sla = await sla_service.create_sla(dispute.id)
    await sla_service.handle_status_change(dispute.id, "OPEN", "WAITING_INTERNAL_TEAM")
    await db_session.refresh(sla)
    assert sla.is_paused is False

    await sla_service.calculate_progress(dispute.id)
    await db_session.refresh(sla)
    assert sla.is_paused is False
    assert sla.status != "CLOSED"


@pytest.mark.asyncio
async def test_sla_stopped_on_closed_dispute(db_session: AsyncSession):
    case_repo = CaseRepository(db_session)
    dispute_repo = DisputeRepository(db_session)
    sla_repo = SLARepository(db_session)
    activity_repo = ActivityRepository(db_session)

    audit_service = AuditService(activity_repo)
    sla_service = SLAService(sla_repo, dispute_repo, audit_service, settings)

    case = await case_repo.create_case(
        case_number=f"CASE-{uuid4().hex[:6].upper()}",
        customer_email="customer@example.com",
    )
    dispute = await dispute_repo.create_dispute(
        dispute_number=f"DISP-{uuid4().hex[:6].upper()}",
        case_id=case.id,
        invoice_id=uuid4(),
        invoice_number="INV-2001",
        customer_id=uuid4(),
        dispute_category="PAYMENT_ALREADY_DONE",
        status="OPEN",
    )

    # Create SLA (started now, 12 hours = 720 mins SLA)
    sla = await sla_service.create_sla(dispute.id)
    assert sla.is_paused is False

    # Simulate passing of 60 minutes
    sla.started_at = datetime.now(UTC) - timedelta(minutes=60)
    await sla_repo.update_sla(sla)

    # Close the dispute
    dispute.status = "CLOSED"
    dispute.closed_at = datetime.now(UTC)
    await dispute_repo.update_dispute(dispute)

    # Trigger handle status change and calculate progress
    await sla_service.handle_status_change(dispute.id, "OPEN", "CLOSED")
    await db_session.refresh(sla)

    assert sla.is_paused is False
    assert sla.status == "CLOSED"

    # Calculate progress at closure time
    await sla_service.calculate_progress(dispute.id)
    await db_session.refresh(sla)

    # Active elapsed should be ~60 mins
    # Percentage should be (60 / 720) * 100 = 8.33%
    assert 8.0 <= sla.current_percentage <= 9.0
    assert sla.status == "CLOSED"

    # Store percentage at closure
    percentage_at_closure = sla.current_percentage

    # Now simulate passing of another 5 hours (300 minutes) and verify progress remains frozen
    # We do this by calculating progress again. Since dispute is CLOSED, progress shouldn't advance.
    # Note: dispute.closed_at remains the same!
    await sla_service.calculate_progress(dispute.id)
    await db_session.refresh(sla)

    assert sla.current_percentage == percentage_at_closure
    assert sla.status == "CLOSED"

    # Test reopening: transition to OPEN
    dispute.status = "OPEN"
    await dispute_repo.update_dispute(dispute)

    await sla_service.handle_status_change(dispute.id, "CLOSED", "OPEN")
    await db_session.refresh(sla)

    assert sla.is_paused is False
    assert sla.paused_at is None
    assert sla.accumulated_paused_minutes == 0.0


@pytest.mark.asyncio
async def test_communication_response_schema_maps_frontend_fields(
    db_session: AsyncSession,
):
    """API response schema should expose message_body and sent_time for the UI."""
    from src.schemas.dispute import DisputeCommunicationResponse

    case_repo = CaseRepository(db_session)
    dispute_repo = DisputeRepository(db_session)
    comm_repo = CommunicationRepository(db_session)

    case = await case_repo.create_case(
        case_number=f"CASE-{uuid4().hex[:6].upper()}",
        customer_email="customer@example.com",
    )
    dispute = await dispute_repo.create_dispute(
        dispute_number=f"DISP-{uuid4().hex[:6].upper()}",
        case_id=case.id,
        invoice_id=uuid4(),
        invoice_number="INV-COMM-TEST",
        customer_id=uuid4(),
        dispute_category="AMENDMENT",
        status="OPEN",
    )
    comm = await comm_repo.create_communication(
        dispute_id=dispute.id,
        recipient="customer@example.com",
        subject="Need UTR",
        body="Please provide your UTR number.",
        communication_type="CUSTOMER",
    )
    await db_session.commit()

    response = DisputeCommunicationResponse.model_validate(comm)
    assert response.message_body == "Please provide your UTR number."
    assert response.sent_time == comm.created_at
    assert response.communication_type == "CUSTOMER"
    assert response.pause_sla_till_reply is False


PAUSE_SLA_TILL_REPLY_STATUSES = [
    "WAITING_ASSOCIATE_APPROVAL",
    "WAITING_INTERNAL_TEAM",
    "WAITING_PAYMENT_REVIEW",
]


async def _create_dispute_with_sla(
    db_session: AsyncSession,
    *,
    status: str = "OPEN",
    dispute_category: str = "PAYMENT_ALREADY_DONE",
):
    case_repo = CaseRepository(db_session)
    dispute_repo = DisputeRepository(db_session)
    sla_repo = SLARepository(db_session)
    activity_repo = ActivityRepository(db_session)
    comm_repo = CommunicationRepository(db_session)

    audit_service = AuditService(activity_repo)
    sla_service = SLAService(sla_repo, dispute_repo, audit_service, settings)

    case = await case_repo.create_case(
        case_number=f"CASE-{uuid4().hex[:6].upper()}",
        customer_email="customer@example.com",
    )
    dispute = await dispute_repo.create_dispute(
        dispute_number=f"DISP-{uuid4().hex[:6].upper()}",
        case_id=case.id,
        invoice_id=uuid4(),
        invoice_number=f"INV-{uuid4().hex[:4].upper()}",
        customer_id=uuid4(),
        dispute_category=dispute_category,
        status=status,
    )
    sla = await sla_service.create_sla(dispute.id)
    return dispute, sla, sla_service, comm_repo


@pytest.mark.asyncio
@pytest.mark.parametrize("waiting_status", PAUSE_SLA_TILL_REPLY_STATUSES)
async def test_pause_for_customer_reply_in_waiting_states(
    db_session: AsyncSession, waiting_status: str
):
    dispute, sla, sla_service, comm_repo = await _create_dispute_with_sla(
        db_session, status=waiting_status
    )
    comm = await comm_repo.create_communication(
        dispute_id=dispute.id,
        recipient="customer@example.com",
        subject="Need more info",
        body="Please reply with details.",
        communication_type="ASSOCIATE_OUTBOUND",
        pause_sla_till_reply=True,
    )

    await sla_service.pause_for_customer_reply(dispute.id, comm.id)
    await db_session.refresh(sla)

    assert sla.is_paused is True
    assert sla.pause_reason == PAUSE_REASON_AWAITING_CUSTOMER_REPLY
    assert sla.paused_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("waiting_status", PAUSE_SLA_TILL_REPLY_STATUSES)
async def test_sla_keeps_running_without_pause_till_reply(
    db_session: AsyncSession, waiting_status: str
):
    dispute, sla, sla_service, _comm_repo = await _create_dispute_with_sla(
        db_session, status=waiting_status
    )

    await sla_service.calculate_progress(dispute.id)
    await db_session.refresh(sla)

    assert sla.is_paused is False
    assert sla.pause_reason is None


@pytest.mark.asyncio
@pytest.mark.parametrize("waiting_status", PAUSE_SLA_TILL_REPLY_STATUSES)
async def test_calculate_progress_does_not_resume_awaiting_customer_reply_pause(
    db_session: AsyncSession, waiting_status: str
):
    dispute, sla, sla_service, comm_repo = await _create_dispute_with_sla(
        db_session, status=waiting_status
    )
    comm = await comm_repo.create_communication(
        dispute_id=dispute.id,
        recipient="customer@example.com",
        subject="Need more info",
        body="Please reply.",
        communication_type="ASSOCIATE_OUTBOUND",
        pause_sla_till_reply=True,
    )

    await sla_service.pause_for_customer_reply(dispute.id, comm.id)
    await sla_service.calculate_progress(dispute.id)
    await db_session.refresh(sla)

    assert sla.is_paused is True
    assert sla.pause_reason == PAUSE_REASON_AWAITING_CUSTOMER_REPLY


@pytest.mark.asyncio
@pytest.mark.parametrize("waiting_status", PAUSE_SLA_TILL_REPLY_STATUSES)
async def test_resume_on_customer_reply_after_associate_pause(
    db_session: AsyncSession, waiting_status: str
):
    dispute, sla, sla_service, comm_repo = await _create_dispute_with_sla(
        db_session, status=waiting_status
    )
    outbound = await comm_repo.create_communication(
        dispute_id=dispute.id,
        recipient="customer@example.com",
        subject="Need more info",
        body="Please reply.",
        communication_type="ASSOCIATE_OUTBOUND",
        pause_sla_till_reply=True,
    )

    await sla_service.pause_for_customer_reply(dispute.id, outbound.id)
    sla.paused_at = datetime.now(UTC) - timedelta(minutes=20)
    await sla_service.sla_repo.update_sla(sla)

    await sla_service.resume_on_customer_reply(dispute.id)
    await db_session.refresh(sla)

    assert sla.is_paused is False
    assert sla.pause_reason is None
    assert sla.accumulated_paused_minutes >= 20.0


@pytest.mark.asyncio
async def test_pause_for_customer_reply_rejected_in_open_status(
    db_session: AsyncSession,
):
    dispute, _sla, sla_service, comm_repo = await _create_dispute_with_sla(
        db_session, status="OPEN"
    )
    comm = await comm_repo.create_communication(
        dispute_id=dispute.id,
        recipient="customer@example.com",
        subject="Need more info",
        body="Please reply.",
        communication_type="ASSOCIATE_OUTBOUND",
        pause_sla_till_reply=True,
    )

    with pytest.raises(ValidationException, match="waiting states"):
        await sla_service.pause_for_customer_reply(dispute.id, comm.id)


@pytest.mark.asyncio
async def test_resume_on_customer_reply_ignored_for_waiting_customer_pause(
    db_session: AsyncSession,
):
    dispute, sla, sla_service, _comm_repo = await _create_dispute_with_sla(
        db_session, status="WAITING_CUSTOMER"
    )

    await sla_service.handle_status_change(dispute.id, "OPEN", "WAITING_CUSTOMER")
    await db_session.refresh(sla)
    assert sla.pause_reason == PAUSE_REASON_WAITING_CUSTOMER

    await sla_service.resume_on_customer_reply(dispute.id)
    await db_session.refresh(sla)

    assert sla.is_paused is True
    assert sla.pause_reason == PAUSE_REASON_WAITING_CUSTOMER


@pytest.mark.asyncio
async def test_second_pause_for_customer_reply_is_noop(db_session: AsyncSession):
    dispute, sla, sla_service, comm_repo = await _create_dispute_with_sla(
        db_session, status="WAITING_ASSOCIATE_APPROVAL"
    )
    first_comm = await comm_repo.create_communication(
        dispute_id=dispute.id,
        recipient="customer@example.com",
        subject="First email",
        body="Please reply.",
        communication_type="ASSOCIATE_OUTBOUND",
        pause_sla_till_reply=True,
    )
    second_comm = await comm_repo.create_communication(
        dispute_id=dispute.id,
        recipient="customer@example.com",
        subject="Second email",
        body="Following up.",
        communication_type="ASSOCIATE_OUTBOUND",
        pause_sla_till_reply=True,
    )

    await sla_service.pause_for_customer_reply(dispute.id, first_comm.id)
    first_paused_at = sla.paused_at

    await sla_service.pause_for_customer_reply(dispute.id, second_comm.id)
    await db_session.refresh(sla)

    assert sla.is_paused is True
    assert first_paused_at is not None
    assert sla.paused_at is not None
    # SQLite may strip tzinfo; compare wall-clock time only.
    assert sla.paused_at.replace(tzinfo=None) == first_paused_at.replace(tzinfo=None)


def test_sla_monitoring_statuses_include_payment_review():
    """WAITING_PAYMENT_REVIEW must be monitored or SLA stays stuck at create-time 0%."""
    assert "WAITING_PAYMENT_REVIEW" in SLA_MONITORING_STATUSES
    assert "WAITING_ASSOCIATE_APPROVAL" in SLA_MONITORING_STATUSES
    assert "ESCALATED" in SLA_MONITORING_STATUSES


@pytest.mark.asyncio
async def test_calculate_progress_advances_for_waiting_payment_review(
    db_session: AsyncSession,
):
    """Payment-review disputes keep the SLA clock running (not paused by status alone)."""
    dispute, sla, sla_service, _ = await _create_dispute_with_sla(
        db_session,
        status="WAITING_PAYMENT_REVIEW",
        dispute_category="PAYMENT_NOT_REFLECTED",
    )
    sla.started_at = datetime.now(UTC) - timedelta(hours=6)
    sla.current_percentage = 0.0
    await sla_service.sla_repo.update_sla(sla)

    updated = await sla_service.calculate_progress(dispute.id)
    assert updated is not None
    assert updated.is_paused is False
    # 6h of 12h payment SLA ≈ 50%
    assert updated.current_percentage == pytest.approx(50.0, abs=1.0)
    assert updated.status == "ON_TRACK"


@pytest.mark.asyncio
async def test_get_sla_recalculates_stale_percentage(db_session: AsyncSession):
    """GET /sla must recompute progress so the UI is not stuck on stored 0%."""
    from src.core.services.dispute_service import DisputeService
    from src.core.services.workflow_context_service import WorkflowContextService
    from src.data.repositories.other_repositories import (
        CommentRepository,
        EvidenceSnapshotRepository,
    )
    from src.data.repositories.workflow_context_repository import (
        WorkflowContextRepository,
    )

    dispute, sla, sla_service, _ = await _create_dispute_with_sla(
        db_session,
        status="WAITING_PAYMENT_REVIEW",
        dispute_category="PAYMENT_NOT_REFLECTED",
    )
    sla.started_at = datetime.now(UTC) - timedelta(hours=3)
    sla.current_percentage = 0.0
    await sla_service.sla_repo.update_sla(sla)

    activity_repo = ActivityRepository(db_session)
    dispute_service = DisputeService(
        DisputeRepository(db_session),
        activity_repo,
        CommentRepository(db_session),
        CommunicationRepository(db_session),
        SLARepository(db_session),
        WorkflowContextService(
            WorkflowContextRepository(db_session), AuditService(activity_repo)
        ),
        EvidenceSnapshotRepository(db_session),
        sla_service,
    )

    live_sla = await dispute_service.get_sla(dispute.id)
    assert live_sla.current_percentage == pytest.approx(25.0, abs=1.0)
    assert live_sla.status == "ON_TRACK"
