import asyncio
from datetime import datetime, timezone, timedelta
from uuid import UUID, uuid4
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config.settings import settings
from src.core.services.assignment_service import AssignmentService
from src.core.services.audit_service import AuditService
from src.core.services.correlation_service import CorrelationService
from src.core.services.escalation_service import EscalationService
from src.core.services.recommendation_service import RecommendationService
from src.core.services.sla_service import SLAService
from src.core.services.workflow_context_service import WorkflowContextService
from src.data.repositories import (
    ActivityRepository,
    AssignmentRepository,
    CaseRepository,
    CommentRepository,
    CommunicationRepository,
    DisputeRepository,
    EscalationRepository,
    RecommendationRepository,
    SLARepository,
    UserRepository,
    WorkflowContextRepository,
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
    assert sla.paused_at is not None

    # Simulate passing of time in pause (mock paused_at to be 30 minutes ago)
    sla.paused_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    await sla_repo.update_sla(sla)

    # 3. Transition back to IN_REVIEW (Resume)
    await sla_service.handle_status_change(dispute.id, "WAITING_CUSTOMER", "IN_REVIEW")
    await db_session.refresh(sla)
    assert sla.is_paused is False
    assert sla.paused_at is None
    assert sla.accumulated_paused_minutes >= 30.0


@pytest.mark.asyncio
async def test_escalation_rules(db_session: AsyncSession, seed_users):
    case_repo = CaseRepository(db_session)
    dispute_repo = DisputeRepository(db_session)
    sla_repo = SLARepository(db_session)
    activity_repo = ActivityRepository(db_session)
    escalation_repo = EscalationRepository(db_session)

    audit_service = AuditService(activity_repo)
    sla_service = SLAService(sla_repo, dispute_repo, audit_service, settings)
    escalation_service = EscalationService(escalation_repo, sla_repo, dispute_repo, audit_service)

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
    sla.started_at = datetime.now(timezone.utc) - timedelta(minutes=1224)
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
    sla.started_at = datetime.now(timezone.utc) - timedelta(minutes=1512)
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
async def test_correlation_engine(db_session: AsyncSession):
    case_repo = CaseRepository(db_session)
    dispute_repo = DisputeRepository(db_session)
    comm_repo = CommunicationRepository(db_session)
    comment_repo = CommentRepository(db_session)
    activity_repo = ActivityRepository(db_session)

    audit_service = AuditService(activity_repo)
    correlation_service = CorrelationService(dispute_repo, comm_repo, comment_repo, audit_service)

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
    matched_id = await correlation_service.correlate_dispute(
        invoice_number="INV-5000",
        raw_category="TAX",
        customer_email="customer@example.com",
        email_subject="Tax Dispute",
        email_body="Tax is wrong.",
    )

    assert matched_id == dispute.id

    # Verify communication was attached
    comms = await comm_repo.get_communications_for_dispute(dispute.id)
    assert len(comms) == 1
    assert comms[0].subject == "Tax Dispute"


@pytest.mark.asyncio
async def test_assignment_service(db_session: AsyncSession, seed_users):
    dispute_repo = DisputeRepository(db_session)
    case_repo = CaseRepository(db_session)
    assign_repo = AssignmentRepository(db_session)
    activity_repo = ActivityRepository(db_session)
    user_repo = UserRepository(db_session)

    audit_service = AuditService(activity_repo)
    assign_service = AssignmentService(dispute_repo, assign_repo, user_repo, audit_service)

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
