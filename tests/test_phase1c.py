"""Comprehensive test suite for Phase 1C dispute resolution paths, agents, and nodes."""

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.workflow.amendment_agent import AmendmentResolutionAgent
from src.core.workflow.graph import get_graph
from src.core.workflow.mail_agent import DisputeMailAgent
from src.core.workflow.payment_reference_agent import PaymentReferenceExtractionAgent
from src.data.repositories.case_repository import CaseRepository
from src.data.repositories.communication_repository import CommunicationRepository
from src.data.repositories.dispute_repository import DisputeRepository

# --- FIXTURES ---


@pytest.fixture
def mock_openrouter_amendment():
    """Mock OpenRouter response for Amendment resolution."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "resolution_outcome": "CUSTOMER_CORRECT",
                            "confidence": 95.0,
                            "reasoning": "Tax calculated incorrectly",
                            "recommended_invoice_json": {
                                "subtotal_amount": 100,
                                "tax_amount": 5,
                                "total_amount": 105,
                            },
                        }
                    )
                }
            }
        ]
    }
    return mock_resp


@pytest.fixture
def mock_openrouter_reference():
    """Mock OpenRouter response for Reference Extraction."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {"reference_number": "UTR123456789", "confidence": 98.0}
                    )
                }
            }
        ]
    }
    return mock_resp


@pytest.fixture
def mock_openrouter_mail():
    """Mock OpenRouter response for Mail Agent."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "recipient": "customer@test.com",
                            "subject": "Dispute Resolved",
                            "body": "Your invoice has been adjusted successfully.",
                        }
                    )
                }
            }
        ]
    }
    return mock_resp


# --- 1. AMENDMENT RESOLUTION AGENT TESTS ---


@pytest.mark.asyncio
async def test_amendment_agent_success(mock_openrouter_amendment):
    """Verifies that the amendment agent processes customer claims correctly via OpenRouter."""
    with (
        patch(
            "httpx.AsyncClient.post",
            new_callable=AsyncMock,
            return_value=mock_openrouter_amendment,
        ),
        patch(
            "src.core.config.settings.settings.OPENROUTER_API_KEY", "sk-openrouter-key"
        ),
        patch("src.core.config.settings.settings.GEMINI_API_KEY", ""),
        patch("src.core.config.settings.settings.GROQ_API_KEY", ""),
    ):
        res = await AmendmentResolutionAgent.resolve_amendment(
            raw_customer_text="Tax on my invoice is wrong",
            invoice_json={"total": 118},
            dispute_category="AMENDMENT",
        )
        assert res["resolution_outcome"] == "CUSTOMER_CORRECT"
        assert res["confidence"] == 95.0
        assert res["recommended_invoice_json"]["tax_amount"] == 5


@pytest.mark.asyncio
async def test_amendment_agent_regex_fallback():
    """Verifies regex fallback on missing keys or failures."""
    with patch("src.core.config.settings.settings.OPENROUTER_API_KEY", ""):
        with patch("src.core.config.settings.settings.GEMINI_API_KEY", ""):
            with patch("src.core.config.settings.settings.GROQ_API_KEY", ""):
                res = await AmendmentResolutionAgent.resolve_amendment(
                    raw_customer_text="Please adjust pricing or tax",
                    invoice_json={"total": 118},
                    dispute_category="AMENDMENT",
                )
                assert res["resolution_outcome"] == "NEED_MORE_INFO"
                assert res["confidence"] == 75.0
                assert "fallback" in res["reasoning"].lower()


# --- 2. PAYMENT REFERENCE EXTRACTION AGENT TESTS ---


@pytest.mark.asyncio
async def test_reference_agent_success(mock_openrouter_reference):
    """Verifies payment reference extraction via OpenRouter."""
    with (
        patch(
            "httpx.AsyncClient.post",
            new_callable=AsyncMock,
            return_value=mock_openrouter_reference,
        ),
        patch(
            "src.core.config.settings.settings.OPENROUTER_API_KEY", "sk-openrouter-key"
        ),
        patch("src.core.config.settings.settings.GEMINI_API_KEY", ""),
        patch("src.core.config.settings.settings.GROQ_API_KEY", ""),
    ):
        res = await PaymentReferenceExtractionAgent.extract_reference(
            "I paid via UTR123456789"
        )
        assert res["reference_number"] == "UTR123456789"
        assert res["confidence"] == 98.0


@pytest.mark.asyncio
async def test_reference_agent_regex_fallback():
    """Verifies regex fallback pulls bank transaction UTR formats."""
    with patch("src.core.config.settings.settings.OPENROUTER_API_KEY", ""):
        with patch("src.core.config.settings.settings.GEMINI_API_KEY", ""):
            with patch("src.core.config.settings.settings.GROQ_API_KEY", ""):
                res = await PaymentReferenceExtractionAgent.extract_reference(
                    "Payment ref SBI1234567890123"
                )
                assert res["reference_number"] == "SBI1234567890123"
                assert res["confidence"] == 85.0


# --- 3. MAIL AGENT TESTS ---


@pytest.mark.asyncio
async def test_mail_agent_llm(mock_openrouter_mail):
    """Verifies mail agent crafts custom emails via LLM."""
    with (
        patch(
            "httpx.AsyncClient.post",
            new_callable=AsyncMock,
            return_value=mock_openrouter_mail,
        ),
        patch(
            "src.core.config.settings.settings.OPENROUTER_API_KEY", "sk-openrouter-key"
        ),
        patch("src.core.config.settings.settings.GEMINI_API_KEY", ""),
        patch("src.core.config.settings.settings.GROQ_API_KEY", ""),
    ):
        res = await DisputeMailAgent.generate_mail(
            dispute_category="QUALITY",
            outcome="CUSTOMER_CORRECT",
            customer_email="test@customer.com",
            activities_summary="Escalated to Quality Team",
            comments_summary="Issue resolved",
            invoice_summary="{}",
        )
        assert res["recipient"] == "customer@test.com"
        assert "Resolved" in res["subject"]


@pytest.mark.asyncio
async def test_mail_agent_fallback_templates():
    """Verifies template fallback on missing LLM keys."""
    with patch("src.core.config.settings.settings.OPENROUTER_API_KEY", ""):
        with patch("src.core.config.settings.settings.GEMINI_API_KEY", ""):
            with patch("src.core.config.settings.settings.GROQ_API_KEY", ""):
                res = await DisputeMailAgent.generate_mail(
                    dispute_category="AMENDMENT",
                    outcome="NEED_MORE_INFO",
                    customer_email="client@company.com",
                    activities_summary="",
                    comments_summary="",
                    invoice_summary="",
                    info_request="No payment reference or UTR was provided. Please reply with the UTR number and invoice number.",
                    invoice_number="INV-12345",
                )
                assert res["recipient"] == "client@company.com"
                assert "UTR number and invoice number" in res["body"]
                assert "INV-12345" in res["body"]


@pytest.mark.asyncio
async def test_mail_agent_payment_not_reflected_company_correct_fallback():
    """Payment disputes must not claim invoice line items are correct when payment unverified."""
    with patch("src.core.config.settings.settings.OPENROUTER_API_KEY", ""):
        with patch("src.core.config.settings.settings.GEMINI_API_KEY", ""):
            with patch("src.core.config.settings.settings.GROQ_API_KEY", ""):
                res = await DisputeMailAgent.generate_mail(
                    dispute_category="PAYMENT_NOT_REFLECTED",
                    outcome="COMPANY_CORRECT",
                    customer_email="customer@stark.com",
                    activities_summary="- PAYMENT_OUTCOME_PROPOSED: {'reason': 'payment_rejected'}",
                    comments_summary="",
                    invoice_summary='{"invoice_number": "INV-2487", "outstanding_amount": 50000}',
                    invoice_number="INV-2487",
                    customer_message_summary=(
                        "Subject: Payment not reflected\n"
                        "UTR HDFCNEFT2026070251782 paid for INV-2487 but still showing outstanding."
                    ),
                )
                body = res["body"].lower()
                assert "unable to verify" in body or "could not verify" in body
                assert "outstanding" in body
                assert "invoice details are correct" not in body
                assert "INV-2487" in res["body"]


@pytest.mark.asyncio
async def test_mail_agent_payment_settled_customer_correct_fallback():
    """Payment matched outcomes should confirm settlement, not generic invoice messaging."""
    with patch("src.core.config.settings.settings.OPENROUTER_API_KEY", ""):
        with patch("src.core.config.settings.settings.GEMINI_API_KEY", ""):
            with patch("src.core.config.settings.settings.GROQ_API_KEY", ""):
                res = await DisputeMailAgent.generate_mail(
                    dispute_category="PAYMENT_NOT_REFLECTED",
                    outcome="CUSTOMER_CORRECT",
                    customer_email="customer@stark.com",
                    activities_summary="",
                    comments_summary="",
                    invoice_summary='{"invoice_number": "INV-2487"}',
                    invoice_number="INV-2487",
                    payment_reference="HDFCNEFT2026070251782",
                    customer_message_summary="Payment for INV-2487 not reflected.",
                )
                body = res["body"].lower()
                assert "payment" in body
                assert "applied" in body or "located" in body
                assert "HDFCNEFT2026070251782" in res["body"]


# --- 4. DETERMINISTIC PATH & ROUTING TESTS ---


@pytest.mark.asyncio
async def test_collections_pausing(db_session: AsyncSession):
    """Tests that collections node pauses collection status in AR Service."""
    case_repo = CaseRepository(db_session)
    dispute_repo = DisputeRepository(db_session)

    case = await case_repo.create_case(
        case_number="CASE-2026-999010", customer_email="customer@test.com"
    )
    dispute = await dispute_repo.create_dispute(
        dispute_number="DISP-2026-999010",
        case_id=case.id,
        invoice_id=uuid4(),
        invoice_number="INV-COLL-TEST",
        customer_id=uuid4(),
        dispute_category="AMENDMENT",
    )
    await db_session.commit()

    get_graph()
    config = {
        "configurable": {
            "db": db_session,
            "thread_id": str(dispute.id),
        }
    }

    # Mock AR pause API call
    mock_resp = {"status": "DISPUTED", "message": "Collections paused"}
    with patch(
        "src.data.clients.ar_service_client.ARServiceClient.pause_collections",
        new_callable=AsyncMock,
        return_value=mock_resp,
    ):
        from src.core.workflow.graph import collections_node

        state = {"dispute_id": dispute.id, "metadata": {}}
        res = await collections_node(state, config)
        assert res["workflow_status"] == "COLLECTIONS_PAUSED"


@pytest.mark.asyncio
async def test_payment_checker_paid_invoice(db_session: AsyncSession):
    """Tests payment checker node returns CUSTOMER_CORRECT immediately if invoice is already paid in AR."""
    case_repo = CaseRepository(db_session)
    dispute_repo = DisputeRepository(db_session)

    case = await case_repo.create_case(
        case_number="CASE-2026-999011", customer_email="customer@test.com"
    )
    dispute = await dispute_repo.create_dispute(
        dispute_number="DISP-2026-999011",
        case_id=case.id,
        invoice_id=uuid4(),
        invoice_number="INV-PAID-TEST",
        customer_id=uuid4(),
        dispute_category="PAYMENT_ALREADY_DONE",
    )
    await db_session.commit()

    config = {
        "configurable": {
            "db": db_session,
            "thread_id": str(dispute.id),
        }
    }

    with patch(
        "src.data.clients.ar_service_client.ARServiceClient.get_payment_status",
        new_callable=AsyncMock,
        return_value="PAID",
    ):
        from src.core.workflow.graph import payment_checker_node

        state = {"dispute_id": dispute.id, "metadata": {}}
        res = await payment_checker_node(state, config)
        assert res["resolution_outcome"] == "CUSTOMER_CORRECT"
        assert res["workflow_status"] == "WAITING_ASSOCIATE_APPROVAL"

        await db_session.refresh(dispute)
        assert dispute.status != "RESOLVED"
        assert dispute.resolution_outcome is None


@pytest.mark.asyncio
async def test_payment_checker_missing_reference(db_session: AsyncSession):
    """Tests payment checker interrupts with WAITING_CUSTOMER if no payment reference exists."""
    case_repo = CaseRepository(db_session)
    dispute_repo = DisputeRepository(db_session)

    case = await case_repo.create_case(
        case_number="CASE-2026-999012", customer_email="customer@test.com"
    )
    dispute = await dispute_repo.create_dispute(
        dispute_number="DISP-2026-999012",
        case_id=case.id,
        invoice_id=uuid4(),
        invoice_number="INV-NOREF-TEST",
        customer_id=uuid4(),
        dispute_category="PAYMENT_ALREADY_DONE",
    )
    await db_session.commit()

    config = {
        "configurable": {
            "db": db_session,
            "thread_id": str(dispute.id),
        }
    }

    with patch(
        "src.data.clients.ar_service_client.ARServiceClient.get_payment_status",
        new_callable=AsyncMock,
        return_value="UNPAID",
    ):
        from langgraph.errors import NodeInterrupt

        from src.core.workflow.graph import payment_checker_node

        state = {"dispute_id": dispute.id, "metadata": {}}
        with pytest.raises(NodeInterrupt):
            await payment_checker_node(state, config)

        await db_session.refresh(dispute)
        assert dispute.status == "WAITING_CUSTOMER"

        comm_repo = CommunicationRepository(db_session)
        comms = await comm_repo.get_communications_for_dispute(dispute.id)
        customer_comms = [c for c in comms if c.communication_type == "CUSTOMER"]
        assert len(customer_comms) == 1
        assert "UTR" in customer_comms[0].body
        assert "INV-NOREF-TEST" in customer_comms[0].body


@pytest.mark.asyncio
async def test_payment_checker_settled_requires_confirmation(db_session: AsyncSession):
    """SETTLED payment reference proposes CUSTOMER_CORRECT but requires associate confirmation."""
    case_repo = CaseRepository(db_session)
    dispute_repo = DisputeRepository(db_session)

    case = await case_repo.create_case(
        case_number="CASE-2026-999013", customer_email="customer@test.com"
    )
    dispute = await dispute_repo.create_dispute(
        dispute_number="DISP-2026-999013",
        case_id=case.id,
        invoice_id=uuid4(),
        invoice_number="INV-SETTLED-TEST",
        customer_id=uuid4(),
        dispute_category="PAYMENT_NOT_REFLECTED",
    )
    await db_session.commit()

    config = {
        "configurable": {
            "db": db_session,
            "thread_id": str(dispute.id),
        }
    }

    with (
        patch(
            "src.data.clients.ar_service_client.ARServiceClient.get_payment_status",
            new_callable=AsyncMock,
            return_value="UNPAID",
        ),
        patch(
            "src.data.clients.ar_service_client.ARServiceClient.find_payment_reference",
            new_callable=AsyncMock,
            return_value="SETTLED",
        ),
    ):
        from src.core.workflow.graph import payment_checker_node

        state = {
            "dispute_id": dispute.id,
            "metadata": {"extracted_reference_number": "UTR123456"},
        }
        res = await payment_checker_node(state, config)
        assert res["resolution_outcome"] == "CUSTOMER_CORRECT"
        assert res["workflow_status"] == "WAITING_ASSOCIATE_APPROVAL"

        await db_session.refresh(dispute)
        assert dispute.status != "RESOLVED"
        assert dispute.resolution_outcome is None


def test_route_after_payment_checker_customer_correct_goes_to_approval():
    from src.core.workflow.graph import route_after_payment_checker

    assert (
        route_after_payment_checker({"resolution_outcome": "CUSTOMER_CORRECT"})
        == "waiting_approval_node"
    )
    assert (
        route_after_payment_checker({"resolution_outcome": "COMPANY_CORRECT"})
        == "mail_agent_node"
    )


# --- 5. END-TO-END WORFLOW ROUTING, RESUMPTION, & APPROVAL TESTS ---


@pytest.mark.asyncio
async def test_e2e_amendment_path(db_session: AsyncSession, seed_users):
    """End-to-End simulation of the Amendment route validation, AI analysis, Associate interrupt, and closing."""
    # Seed invoice table in SQLite
    is_sqlite = db_session.bind.dialect.name == "sqlite"
    table_name = "invoices" if is_sqlite else "ar.invoices"
    now_func = "datetime('now')" if is_sqlite else "NOW()"
    invoice_id = uuid4()
    await db_session.execute(
        text(
            f"INSERT INTO {table_name} (id, invoice_number, customer_id, invoice_date, due_date, currency, subtotal_amount, tax_amount, total_amount, outstanding_amount, status, batch_id, is_deleted, created_at, updated_at) "
            f"VALUES (:id, 'INV-AMEND-1', :cust_id, '2026-01-01', '2026-01-31', 'INR', 100, 18, 118, 118, 'PENDING', :batch_id, false, {now_func}, {now_func})"
        ),
        {
            "id": str(invoice_id) if is_sqlite else invoice_id,
            "cust_id": str(uuid4()),
            "batch_id": str(uuid4()),
        },
    )
    await db_session.commit()

    case_repo = CaseRepository(db_session)
    dispute_repo = DisputeRepository(db_session)

    case = await case_repo.create_case(
        case_number="CASE-2026-999015", customer_email="customer@test.com"
    )
    dispute = await dispute_repo.create_dispute(
        dispute_number="DISP-2026-999015",
        case_id=case.id,
        invoice_id=invoice_id,
        invoice_number="INV-AMEND-1",
        customer_id=uuid4(),
        dispute_category="AMENDMENT",
        status="OPEN",
    )
    await db_session.commit()

    # Define Graph execution
    graph = get_graph()
    config = {
        "configurable": {
            "db": db_session,
            "thread_id": str(dispute.id),
        }
    }

    # Initial state
    state = {
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

    # Mock outer systems (AR client APIs, AI agents)
    mock_ar = MagicMock()
    mock_ar.get_invoice_details = AsyncMock(
        return_value={
            "invoice_number": "INV-AMEND-1",
            "subtotal_amount": 100,
            "tax_amount": 18,
            "total_amount": 118,
            "outstanding_amount": 118,
            "items": [],
        }
    )
    mock_ar.pause_collections = AsyncMock(return_value={})
    mock_ar.resume_collections = AsyncMock(return_value={})
    mock_ar.amend_invoice = AsyncMock(
        return_value={
            "invoice_id": str(invoice_id),
            "version": 2,
            "status": "PAID",
            "outstanding_amount": 0,
            "credit_amount": 0,
        }
    )

    mock_amend_outcome = {
        "resolution_outcome": "CUSTOMER_CORRECT",
        "confidence": 99.0,
        "reasoning": "Customer correctly identified quantity error",
        "recommended_invoice_json": {
            "subtotal_amount": 50,
            "tax_amount": 9,
            "total_amount": 59,
            "items": [
                {
                    "description": "Widget",
                    "quantity": 1,
                    "unit_price": 50,
                    "amount": 50,
                }
            ],
        },
        "agent_run_details": None,
    }

    mock_mail_outcome = {
        "recipient": "customer@test.com",
        "subject": "Dispute Approved",
        "body": "We corrected the invoice total to 59.",
        "agent_run_details": None,
    }

    with patch("src.core.workflow.graph.ARServiceClient", return_value=mock_ar):
        with patch(
            "src.core.workflow.amendment_agent.AmendmentResolutionAgent.resolve_amendment",
            new_callable=AsyncMock,
            return_value=mock_amend_outcome,
        ):
            with patch(
                "src.core.workflow.mail_agent.DisputeMailAgent.generate_mail",
                new_callable=AsyncMock,
                return_value=mock_mail_outcome,
            ):
                # 1. First execution: should pause at waiting_approval_node
                await graph.ainvoke(state, config)

                await db_session.refresh(dispute)
                assert dispute.status == "WAITING_ASSOCIATE_APPROVAL"

                # 2. Resumption simulation (Associate approves)
                from src.core.workflow.resume_service import DisputeResumeService

                resume_service = DisputeResumeService()
                state_updates = {
                    "resolution_outcome": "APPROVE",
                    "requires_human_review": False,
                    "review_reason": None,
                }

                await resume_service.resume_workflow(
                    db=db_session,
                    dispute_id=dispute.id,
                    state_updates=state_updates,
                    as_node="waiting_approval_node",
                )

                # Verify final dispute state
                await db_session.refresh(dispute)
                assert dispute.status == "CLOSED"
                assert dispute.resolution_outcome == "CUSTOMER_CORRECT"
                assert dispute.closed_at is not None
                mock_ar.amend_invoice.assert_called_once()

                # Verify communication was persisted
                comm_repo = CommunicationRepository(db_session)
                comms = await comm_repo.get_communications_for_dispute(dispute.id)
                assert len(comms) > 0
                assert comms[0].subject == "Dispute Approved"
