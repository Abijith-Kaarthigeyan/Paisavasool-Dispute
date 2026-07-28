from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.repositories.assignment_repository import AssignmentRepository
from src.data.repositories.case_repository import CaseRepository
from src.data.repositories.dispute_repository import DisputeRepository
from src.data.repositories.review_queue_repository import ReviewQueueRepository
from src.data.repositories.sla_repository import SLARepository


@pytest.mark.asyncio
async def test_case_repository(db_session: AsyncSession):
    repo = CaseRepository(db_session)
    case_no = f"CASE-{uuid4().hex[:6].upper()}"

    # Create Case
    case = await repo.create_case(
        case_number=case_no,
        customer_email="customer@example.com",
        email_subject="Wrong Pricing",
        email_body="The invoice price is incorrect.",
        original_message_id="msg-123",
    )

    assert case.id is not None
    assert case.case_number == case_no
    assert case.status == "OPEN"

    # Get by case number
    fetched = await repo.get_by_case_number(case_no)
    assert fetched is not None
    assert fetched.id == case.id

    # List cases
    cases_list, total = await repo.list_cases()
    assert len(cases_list) >= 1
    assert total >= 1


@pytest.mark.asyncio
async def test_dispute_repository(db_session: AsyncSession):
    case_repo = CaseRepository(db_session)
    dispute_repo = DisputeRepository(db_session)

    case = await case_repo.create_case(
        case_number=f"CASE-{uuid4().hex[:6].upper()}",
        customer_email="customer@example.com",
    )

    disp_no = f"DISP-{uuid4().hex[:6].upper()}"
    invoice_id = uuid4()
    customer_id = uuid4()

    # Create Dispute
    dispute = await dispute_repo.create_dispute(
        dispute_number=disp_no,
        case_id=case.id,
        invoice_id=invoice_id,
        invoice_number="INV-1001",
        customer_id=customer_id,
        dispute_category="AMENDMENT",
        status="OPEN",
    )

    assert dispute.id is not None
    assert dispute.dispute_number == disp_no
    assert dispute.dispute_category == "AMENDMENT"

    # Find active dispute
    active = await dispute_repo.find_active_dispute_by_invoice_and_category(
        invoice_number="INV-1001",
        dispute_category="AMENDMENT",
        active_statuses=["OPEN"],
    )
    assert active is not None
    assert active.id == dispute.id

    # Verify exclude_dispute_id works
    active_excluded = await dispute_repo.find_active_dispute_by_invoice_and_category(
        invoice_number="INV-1001",
        dispute_category="AMENDMENT",
        active_statuses=["OPEN"],
        exclude_dispute_id=dispute.id,
    )
    assert active_excluded is None

    # Create a second active dispute with same invoice and category to check first() defensive match
    dispute2 = await dispute_repo.create_dispute(
        dispute_number=f"DISP-{uuid4().hex[:6].upper()}",
        case_id=case.id,
        invoice_id=invoice_id,
        invoice_number="INV-1001",
        customer_id=customer_id,
        dispute_category="AMENDMENT",
        status="OPEN",
    )

    # Calling find_active_dispute_by_invoice_and_category should not raise MultipleResultsFound, but return first match
    active_first = await dispute_repo.find_active_dispute_by_invoice_and_category(
        invoice_number="INV-1001",
        dispute_category="AMENDMENT",
        active_statuses=["OPEN"],
    )
    assert active_first is not None
    assert active_first.id in (dispute.id, dispute2.id)


@pytest.mark.asyncio
async def test_assignment_repository(db_session: AsyncSession):
    case_repo = CaseRepository(db_session)
    dispute_repo = DisputeRepository(db_session)
    assign_repo = AssignmentRepository(db_session)

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
        dispute_category="AMENDMENT",
    )

    assoc_id = uuid4()
    # Create Assignment
    assign = await assign_repo.create_assignment(
        dispute_id=dispute.id,
        assigned_to=assoc_id,
        active=True,
    )

    assert assign.id is not None
    assert assign.assigned_to == assoc_id
    assert assign.active is True

    # Deactivate assignments
    await assign_repo.deactivate_all_assignments_for_dispute(dispute.id)
    active_assign = await assign_repo.get_active_assignment_for_dispute(dispute.id)
    assert active_assign is None


@pytest.mark.asyncio
async def test_sla_repository(db_session: AsyncSession):
    case_repo = CaseRepository(db_session)
    dispute_repo = DisputeRepository(db_session)
    sla_repo = SLARepository(db_session)

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
        dispute_category="AMENDMENT",
    )

    # Create SLA
    now = datetime.now(UTC)
    sla = await sla_repo.create_sla(
        dispute_id=dispute.id,
        sla_minutes=1440,
        started_at=now,
    )

    assert sla.id is not None
    assert sla.sla_minutes == 1440
    assert sla.is_paused is False

    # Get by dispute ID
    fetched = await sla_repo.get_by_dispute_id(dispute.id)
    assert fetched is not None
    assert fetched.id == sla.id


@pytest.mark.asyncio
async def test_review_queue_repository(db_session: AsyncSession):
    case_repo = CaseRepository(db_session)
    dispute_repo = DisputeRepository(db_session)
    review_repo = ReviewQueueRepository(db_session)

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
        dispute_category="AMENDMENT",
    )

    # Create Review Queue Item
    item = await review_repo.create_review_queue_item(
        dispute_id=dispute.id,
        review_reason="TEST_REASON",
        status="PENDING",
    )

    assert item.id is not None
    assert item.review_reason == "TEST_REASON"
    assert item.status == "PENDING"

    # List Review Queue
    items = await review_repo.list_review_queue()
    assert len(items) >= 1
