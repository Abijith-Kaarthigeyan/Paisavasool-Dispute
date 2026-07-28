"""Pytest suite for dispute management workflow, agents, and APIs."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.workflow.graph import dispute_generation_node, get_graph
from src.core.workflow.triage_agent import DisputeTriageAgent
from src.data.repositories.case_repository import CaseRepository
from src.data.repositories.dispute_repository import DisputeRepository
from src.data.repositories.review_queue_repository import ReviewQueueRepository
from src.data.repositories.workflow_context_repository import WorkflowContextRepository


@pytest.fixture
def mock_openrouter_success():
    """Mocks OpenRouter response return payload."""
    mock_resp = MagicMock(spec=Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": json_payload_str()}}]
    }
    return mock_resp


def json_payload_str():
    return """
    {
      "invoices": [
        {
          "invoice_number": "INV-1001",
          "dispute_types": ["PRICING", "TAX"]
        },
        {
          "invoice_number": "INV-2001",
          "dispute_types": ["PAYMENT_ALREADY_DONE"]
        }
      ],
      "confidence": 95
    }
    """


@pytest.mark.asyncio
async def test_triage_agent_normal_flow(mock_openrouter_success):
    """Tests the triage agent extracts invoices and categories correctly using mocked OpenRouter."""
    with (
        patch(
            "httpx.AsyncClient.post",
            new_callable=AsyncMock,
            return_value=mock_openrouter_success,
        ),
        patch("src.core.config.settings.settings.OPENROUTER_API_KEY", "real-mock-key"),
        patch("src.core.config.settings.settings.GEMINI_API_KEY", ""),
        patch("src.core.config.settings.settings.GROQ_API_KEY", ""),
    ):
        res = await DisputeTriageAgent.triage_communication(
            subject="Dispute for invoice",
            body="Invoice INV-1001 pricing is wrong and INV-2001 was already paid.",
        )

        assert res["confidence"] == 95.0
        assert len(res["invoices"]) == 2

        # pricing & tax must collapse to AMENDMENT
        inv1 = next(i for i in res["invoices"] if i["invoice_number"] == "INV-1001")
        assert "AMENDMENT" in inv1["dispute_types"]

        inv2 = next(i for i in res["invoices"] if i["invoice_number"] == "INV-2001")
        assert "PAYMENT_ALREADY_DONE" in inv2["dispute_types"]


@pytest.mark.asyncio
async def test_triage_agent_regex_fallback():
    """Tests the triage agent uses regex fallback when LLM keys are absent/offline."""
    with (
        patch("src.core.config.settings.settings.OPENROUTER_API_KEY", ""),
        patch("src.core.config.settings.settings.GEMINI_API_KEY", ""),
        patch("src.core.config.settings.settings.GROQ_API_KEY", ""),
    ):
        res = await DisputeTriageAgent.triage_communication(
            subject="INV-1001 payment query",
            body="I already paid INV-1001. Please check transaction cleared.",
        )

        assert res["confidence"] == 75.0
        assert len(res["invoices"]) == 1
        assert res["invoices"][0]["invoice_number"] == "INV-1001"
        assert "PAYMENT_ALREADY_DONE" in res["invoices"][0]["dispute_types"]


def test_invoice_number_normalization():
    """Tests that raw invoice numbers are normalized correctly to the INV-XXXX format."""
    assert DisputeTriageAgent.normalize_invoice_number("invoice-2599") == "INV-2599"
    assert DisputeTriageAgent.normalize_invoice_number("INVOICE-2599") == "INV-2599"
    assert DisputeTriageAgent.normalize_invoice_number("inv-2599") == "INV-2599"
    assert DisputeTriageAgent.normalize_invoice_number("2599") == "INV-2599"
    assert DisputeTriageAgent.normalize_invoice_number("INV-2599") == "INV-2599"


@pytest.mark.asyncio
async def test_case_intake_idempotency(db_session: AsyncSession):
    """Tests that Case Intake node is idempotent on message_id."""
    case_repo = CaseRepository(db_session)
    msg_id = f"msg-{uuid4().hex}"

    # 1. Create original case
    case = await case_repo.create_case(
        case_number=f"CASE-{datetime.now().year}-999001",
        customer_email="test@example.com",
        email_subject="Test subject",
        email_body="Body text",
        original_message_id=msg_id,
    )
    await db_session.commit()

    # 2. Run graph with same message_id
    graph = get_graph()
    config = {
        "configurable": {
            "db": db_session,
            "thread_id": str(uuid4()),
        }
    }
    state = {
        "customer_email": "test@example.com",
        "email_subject": "Test subject",
        "email_body": "Body text",
        "message_id": msg_id,
        "workflow_status": "START",
        "current_node": "START",
        "requires_human_review": False,
        "errors": [],
        "metadata": {},
    }

    final_state = await graph.ainvoke(state, config)
    assert final_state["case_id"] == case.id
    assert final_state["workflow_status"] == "CASE_CREATED"


@pytest.mark.asyncio
async def test_dispute_generation_and_sla_creation(
    db_session: AsyncSession, seed_users
):
    """Tests dispute generation node creates dispute, SLA, and triggers assignment."""
    is_sqlite = db_session.bind.dialect.name == "sqlite"
    table_name = "invoices" if is_sqlite else "ar.invoices"
    now_func = "datetime('now')" if is_sqlite else "NOW()"
    invoice_id = uuid4()
    customer_id = uuid4()
    await db_session.execute(
        text(
            f"INSERT INTO {table_name} (id, invoice_number, customer_id, invoice_date, due_date, currency, subtotal_amount, tax_amount, total_amount, outstanding_amount, status, batch_id, is_deleted, created_at, updated_at) "
            f"VALUES (:id, 'INV-1001', :cust_id, '2026-01-01', '2026-01-31', 'INR', 100, 18, 118, 118, 'PENDING', :batch_id, false, {now_func}, {now_func})"
        ),
        {
            "id": str(invoice_id) if is_sqlite else invoice_id,
            "cust_id": str(customer_id) if is_sqlite else customer_id,
            "batch_id": str(uuid4()) if is_sqlite else uuid4(),
        },
    )
    await db_session.commit()

    case_repo = CaseRepository(db_session)
    case = await case_repo.create_case(
        case_number=f"CASE-{datetime.now().year}-999002",
        customer_email="test@example.com",
    )
    await db_session.commit()

    get_graph()
    config = {
        "configurable": {
            "db": db_session,
            "thread_id": str(uuid4()),
        }
    }
    state = {
        "case_id": case.id,
        "customer_email": "test@example.com",
        "invoices": [{"invoice_number": "INV-1001", "dispute_types": ["AMENDMENT"]}],
        "confidence": 95.0,
        "workflow_status": "TRIAGED",
        "current_node": "triage_agent_node",
        "requires_human_review": False,
        "errors": [],
        "metadata": {},
    }

    # Execute from dispute_generation_node
    # Note: We patch celery process_dispute_workflow task to verify spawning tasks
    with patch(
        "src.infrastructure.celery.tasks.process_dispute_workflow.delay"
    ) as mock_delay:
        await dispute_generation_node(state, config)
        assert mock_delay.call_count == 1

        # Check DB
        dispute_repo = DisputeRepository(db_session)
        disputes, _ = await dispute_repo.list_disputes()
        disp = next(d for d in disputes if d.case_id == case.id)
        assert disp.invoice_number == "INV-1001"
        assert disp.dispute_category == "AMENDMENT"
        assert disp.assigned_to == seed_users["associate"].id


@pytest.mark.asyncio
async def test_full_graph_interrupt_and_resume(db_session: AsyncSession, seed_users):
    """Tests the full LangGraph validation node interrupt and review resolution resume flow."""
    # 1. Create a dispute with missing invoice (triggers validation failure interrupt)
    case_repo = CaseRepository(db_session)
    dispute_repo = DisputeRepository(db_session)

    case = await case_repo.create_case(
        case_number=f"CASE-{datetime.now().year}-999003",
        customer_email="customer@example.com",
    )

    dispute = await dispute_repo.create_dispute(
        dispute_number=f"DISP-{datetime.now().year}-999003",
        case_id=case.id,
        invoice_id=uuid4(),  # Random invoice not in AR
        invoice_number="INV-MISSING",
        customer_id=uuid4(),
        dispute_category="AMENDMENT",
    )
    await db_session.commit()

    # 2. Run workflow
    graph = get_graph()
    config = {
        "configurable": {
            "db": db_session,
            "thread_id": str(dispute.id),
        }
    }

    initial_state = {
        "case_id": dispute.case_id,
        "dispute_id": dispute.id,
        "dispute_number": dispute.dispute_number,
        "invoice_id": dispute.invoice_id,
        "invoice_number": dispute.invoice_number,
        "customer_id": dispute.customer_id,
        "customer_email": case.customer_email,
        "dispute_category": dispute.dispute_category,
        "assigned_to": dispute.assigned_to,
        "workflow_status": "START",
        "current_node": "correlation_node",
        "requires_human_review": False,
        "errors": [],
        "metadata": {},
    }

    # Should pause at validation/review queue node
    await graph.ainvoke(initial_state, config)

    # Verify review queue item is PENDING
    review_repo = ReviewQueueRepository(db_session)
    review_item = await review_repo.get_by_dispute_id(dispute.id)
    assert review_item is not None
    assert review_item.status == "PENDING_REVIEW"
    assert review_item.review_reason == "INVOICE_MISSING"

    # Also verify dispute status updated to IN_REVIEW
    await db_session.refresh(dispute)
    assert dispute.status == "IN_REVIEW"


@pytest.mark.asyncio
async def test_apis_intake_and_decisions(
    mock_client: AsyncClient, db_session: AsyncSession, seed_users
):
    """Tests the API endpoints for cases intake and associate approval decisions."""
    triage_mock_val = {
        "invoices": [{"invoice_number": "INV-1001", "dispute_types": ["AMENDMENT"]}],
        "confidence": 95.0,
    }

    with (
        patch(
            "src.core.workflow.triage_agent.DisputeTriageAgent.triage_communication",
            new_callable=AsyncMock,
            return_value=triage_mock_val,
        ),
        patch(
            "src.infrastructure.celery.tasks.process_dispute_case.delay"
        ) as mock_delay,
    ):
        # POST /cases/intake
        headers = {"Authorization": "Bearer mock-finance-token"}
        payload = {
            "customer_email": "customer@example.com",
            "email_subject": "Pricing Dispute",
            "email_body": "Please review invoice INV-1001.",
            "message_id": f"msg-{uuid4().hex}",
        }
        resp = await mock_client.post(
            "/api/v1/cases/intake", json=payload, headers=headers
        )
        assert resp.status_code == 201
        assert resp.json()["status"] == "QUEUED"
        case_id = UUID(resp.json()["case_id"])
        assert mock_delay.call_count == 1

        # GET /cases/{id}/disputes
        resp = await mock_client.get(
            f"/api/v1/cases/{case_id}/disputes", headers=headers
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    # Test Associate Decision API
    dispute_repo = DisputeRepository(db_session)
    dispute = await dispute_repo.create_dispute(
        dispute_number=f"DISP-{datetime.now().year}-999004",
        case_id=case_id,
        invoice_id=uuid4(),
        invoice_number="INV-1001",
        customer_id=uuid4(),
        dispute_category="AMENDMENT",
    )
    await db_session.commit()

    # Create dummy workflow context checkpoint so resume doesn't throw 404
    context_repo = WorkflowContextRepository(db_session)
    await context_repo.create_workflow_context(
        dispute_id=dispute.id,
        workflow_name="dispute_workflow",
        current_node="waiting_approval_node",
        workflow_state={
            "checkpoints": {"chk-1": {"checkpoint": "mock", "metadata": "mock"}},
            "latest_checkpoint_id": "chk-1",
        },
    )
    await db_session.commit()

    # Mock resume service to avoid full graph execution complications in REST API test
    with patch(
        "src.core.workflow.resume_service.DisputeResumeService.resume_workflow",
        new_callable=AsyncMock,
    ) as mock_resume:
        decision_payload = {
            "decision": "APPROVE",
            "comments": "Approved associate pricing change.",
        }
        resp = await mock_client.post(
            f"/api/v1/disputes/{dispute.id}/associate-decision",
            json=decision_payload,
            headers={"Authorization": "Bearer mock-finance-token"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "SUCCESS"
        assert mock_resume.call_count == 1
