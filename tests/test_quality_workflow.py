"""Tests for the GRN-backed quality dispute workflow and routing regressions."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from langgraph.graph import END
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.workflow.graph import (
    quality_evidence_fetcher_node,
    quality_investigation_node,
    route_after_amendment_resolution,
    route_after_quality_evidence,
    route_after_quality_investigation,
    route_after_waiting_approval,
    route_collections_destination,
    waiting_approval_node,
)
from src.core.workflow.quality_checker_agent import QualityCheckerAgent
from src.data.repositories.case_repository import CaseRepository
from src.data.repositories.dispute_repository import DisputeRepository
from src.data.repositories.other_repositories import EvidenceSnapshotRepository
from src.data.repositories.recommendation_repository import RecommendationRepository


async def _create_dispute(
    db: AsyncSession,
    *,
    category: str = "QUALITY",
    status: str = "OPEN",
    customer_text: str = "The delivered widgets were damaged.",
):
    suffix = uuid4().hex[:12]
    case = await CaseRepository(db).create_case(
        case_number=f"CASE-QUALITY-{suffix}",
        customer_email="quality-customer@example.com",
        email_subject="Damaged goods",
        email_body=customer_text,
    )
    dispute = await DisputeRepository(db).create_dispute(
        dispute_number=f"DISP-QUALITY-{suffix}",
        case_id=case.id,
        invoice_id=uuid4(),
        invoice_number=f"INV-{suffix}",
        customer_id=uuid4(),
        dispute_category=category,
        status=status,
    )
    await db.commit()
    return case, dispute


def _config(db: AsyncSession, dispute_id) -> dict:
    return {
        "configurable": {
            "db": db,
            "thread_id": str(dispute_id),
        }
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    [
        "CUSTOMER_CORRECT",
        "COMPANY_CORRECT",
        "NEED_MORE_INFO",
        "ESCALATE_TO_QUALITY_TEAM",
    ],
)
async def test_quality_checker_agent_accepts_supported_outcomes(outcome):
    completion = SimpleNamespace(
        content=json.dumps(
            {
                "resolution_outcome": outcome,
                "confidence": 91,
                "reasoning": "The evidence supports this recommendation.",
            }
        ),
        latency=0.05,
        model="test-model",
        provider="test-provider",
    )

    with patch(
        "src.core.workflow.quality_checker_agent.generate_text_completion",
        new_callable=AsyncMock,
        return_value=completion,
    ):
        result = await QualityCheckerAgent.resolve_quality(
            raw_customer_text="Two units arrived damaged.",
            invoice_json={"invoice_number": "INV-1"},
            purchase_order_json={"po_number": "PO-1"},
            grns_json=[{"grn_number": "GRN-1", "status": "LINKED"}],
        )

    assert result["resolution_outcome"] == outcome
    assert result["confidence"] == 91.0
    assert result["agent_run_details"]["status"] == "SUCCESS"


@pytest.mark.asyncio
async def test_quality_checker_agent_invalid_outcome_escalates():
    completion = SimpleNamespace(
        content=json.dumps(
            {
                "resolution_outcome": "AUTO_CLOSE",
                "confidence": 99,
                "reasoning": "Unsupported outcome.",
            }
        ),
        latency=0.01,
        model="test-model",
        provider="test-provider",
    )

    with patch(
        "src.core.workflow.quality_checker_agent.generate_text_completion",
        new_callable=AsyncMock,
        return_value=completion,
    ):
        result = await QualityCheckerAgent.resolve_quality(
            raw_customer_text="The shipment has a quality problem.",
            invoice_json={},
            purchase_order_json={},
            grns_json=[{"grn_number": "GRN-1", "status": "LINKED"}],
        )

    assert result["resolution_outcome"] == "ESCALATE_TO_QUALITY_TEAM"


@pytest.mark.asyncio
async def test_quality_checker_agent_fallback_without_grn_escalates():
    with patch(
        "src.core.workflow.quality_checker_agent.generate_text_completion",
        new_callable=AsyncMock,
        return_value=None,
    ):
        result = await QualityCheckerAgent.resolve_quality(
            raw_customer_text="The goods were damaged.",
            invoice_json={"invoice_number": "INV-1"},
            purchase_order_json={"po_number": "PO-1"},
            grns_json=[],
        )

    assert result["resolution_outcome"] == "ESCALATE_TO_QUALITY_TEAM"
    assert result["agent_run_details"]["status"] == "FALLBACK"
    assert result["agent_run_details"]["model"] == "regex_fallback"


@pytest.mark.asyncio
async def test_quality_evidence_without_po_routes_to_department(
    db_session: AsyncSession,
):
    _, dispute = await _create_dispute(db_session)
    ar_client = MagicMock()
    ar_client.get_invoice_details = AsyncMock(
        return_value={
            "invoice_number": dispute.invoice_number,
            "po_id": None,
            "total_amount": 118,
        }
    )

    with patch("src.core.workflow.graph.ARServiceClient", return_value=ar_client):
        result = await quality_evidence_fetcher_node(
            {"dispute_id": dispute.id, "metadata": {}},
            _config(db_session, dispute.id),
        )

    assert result["resolution_outcome"] == "PENDING_INTERNAL_REVIEW"
    assert result["metadata"]["quality_fallback_reason"] == "missing_po_id"
    assert route_after_quality_evidence(result) == "department_contact_node"
    ar_client.get_purchase_order.assert_not_called()
    ar_client.get_purchase_order_grns.assert_not_called()


@pytest.mark.asyncio
async def test_quality_evidence_without_linked_grn_routes_to_department(
    db_session: AsyncSession,
):
    _, dispute = await _create_dispute(db_session)
    po_id = uuid4()
    ar_client = MagicMock()
    ar_client.get_invoice_details = AsyncMock(
        return_value={
            "invoice_number": dispute.invoice_number,
            "po_id": str(po_id),
            "po_number": "PO-1",
            "total_amount": 118,
        }
    )
    ar_client.get_purchase_order = AsyncMock(
        return_value={"id": str(po_id), "po_number": "PO-1"}
    )
    ar_client.get_purchase_order_grns = AsyncMock(
        return_value=[{"grn_number": "GRN-UNLINKED", "status": "UNLINKED"}]
    )

    with patch("src.core.workflow.graph.ARServiceClient", return_value=ar_client):
        result = await quality_evidence_fetcher_node(
            {"dispute_id": dispute.id, "metadata": {}},
            _config(db_session, dispute.id),
        )

    assert result["metadata"]["grns_json"] == []
    assert result["metadata"]["quality_fallback_reason"] == "no_linked_grns"
    assert route_after_quality_evidence(result) == "department_contact_node"


@pytest.mark.asyncio
async def test_quality_evidence_with_linked_grn_routes_to_investigation(
    db_session: AsyncSession,
):
    _, dispute = await _create_dispute(db_session)
    po_id = uuid4()
    linked_grn = {
        "id": str(uuid4()),
        "grn_number": "GRN-LINKED",
        "status": "LINKED",
        "notes": "Two cartons damaged",
    }
    ar_client = MagicMock()
    ar_client.get_invoice_details = AsyncMock(
        return_value={
            "invoice_number": dispute.invoice_number,
            "po_id": str(po_id),
            "po_number": "PO-1",
            "total_amount": 118,
        }
    )
    ar_client.get_purchase_order = AsyncMock(
        return_value={"id": str(po_id), "po_number": "PO-1"}
    )
    ar_client.get_purchase_order_grns = AsyncMock(
        return_value=[
            linked_grn,
            {"grn_number": "GRN-IGNORED", "status": "UNLINKED"},
        ]
    )

    with patch("src.core.workflow.graph.ARServiceClient", return_value=ar_client):
        result = await quality_evidence_fetcher_node(
            {"dispute_id": dispute.id, "metadata": {"preserved": True}},
            _config(db_session, dispute.id),
        )

    assert result["metadata"]["grns_json"] == [linked_grn]
    assert result["metadata"]["preserved"] is True
    assert "quality_fallback" not in result["metadata"]
    assert route_after_quality_evidence(result) == "quality_investigation_node"

    snapshots = await EvidenceSnapshotRepository(db_session).list_for_dispute(
        dispute.id
    )
    assert len(snapshots) == 1
    assert snapshots[0].snapshot_type == "quality_evidence_snapshot"
    assert snapshots[0].snapshot_data["grns_json"] == [linked_grn]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["CUSTOMER_CORRECT", "COMPANY_CORRECT"])
async def test_quality_investigation_always_requires_associate_approval(
    db_session: AsyncSession,
    outcome: str,
):
    _, dispute = await _create_dispute(db_session)
    metadata = {
        "invoice_json": {"invoice_number": dispute.invoice_number},
        "purchase_order_json": {"po_number": "PO-1"},
        "grns_json": [{"grn_number": "GRN-1", "status": "LINKED"}],
    }
    agent_result = {
        "resolution_outcome": outcome,
        "confidence": 94.0,
        "reasoning": "Goods receipt evidence supports the recommendation.",
        "agent_run_details": None,
    }

    with patch(
        "src.core.workflow.graph.QualityCheckerAgent.resolve_quality",
        new_callable=AsyncMock,
        return_value=agent_result,
    ) as resolve_quality:
        result = await quality_investigation_node(
            {
                "dispute_id": dispute.id,
                "dispute_category": "QUALITY",
                "metadata": metadata,
            },
            _config(db_session, dispute.id),
        )

    assert result["workflow_status"] == "WAITING_ASSOCIATE_APPROVAL"
    assert result["metadata"]["recommended_outcome"] == outcome
    assert route_after_quality_investigation(result) == "waiting_approval_node"
    assert resolve_quality.await_args.kwargs["grns_json"] == metadata["grns_json"]

    recommendation = await RecommendationRepository(
        db_session
    ).get_latest_recommendation(dispute.id)
    assert recommendation is not None
    assert recommendation.recommended_action.startswith(f"QUALITY_DECISION: {outcome}")


def test_quality_investigation_non_decisions_route_safely():
    assert (
        route_after_quality_investigation(
            {"resolution_outcome": "ESCALATE_TO_QUALITY_TEAM"}
        )
        == "department_contact_node"
    )
    assert (
        route_after_quality_investigation({"resolution_outcome": "NEED_MORE_INFO"})
        == END
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("recommendation", "decision", "expected"),
    [
        ("CUSTOMER_CORRECT", "APPROVE", "CUSTOMER_CORRECT"),
        ("CUSTOMER_CORRECT", "REJECT", "COMPANY_CORRECT"),
        ("COMPANY_CORRECT", "APPROVE", "COMPANY_CORRECT"),
        ("COMPANY_CORRECT", "REJECT", "CUSTOMER_CORRECT"),
    ],
)
async def test_quality_associate_decision_accepts_or_flips_recommendation(
    db_session: AsyncSession,
    recommendation: str,
    decision: str,
    expected: str,
):
    _, dispute = await _create_dispute(
        db_session,
        status="WAITING_ASSOCIATE_APPROVAL",
    )

    result = await waiting_approval_node(
        {
            "dispute_id": dispute.id,
            "dispute_category": "QUALITY",
            "resolution_outcome": decision,
            "metadata": {"recommended_outcome": recommendation},
        },
        _config(db_session, dispute.id),
    )

    assert result["resolution_outcome"] == expected
    assert result["workflow_status"] == "RESOLVED"
    await db_session.refresh(dispute)
    assert dispute.resolution_outcome == expected
    assert dispute.status == "RESOLVED"


def test_collections_routes_quality_without_changing_amendment_or_late_delivery():
    assert (
        route_collections_destination({"dispute_category": "QUALITY"})
        == "quality_evidence_fetcher_node"
    )
    assert (
        route_collections_destination({"dispute_category": "AMENDMENT"})
        == "invoice_fetcher_node"
    )
    assert (
        route_collections_destination({"dispute_category": "LATE_DELIVERY"})
        == "late_delivery_evidence_fetcher_node"
    )


def test_amendment_routing_regression():
    assert (
        route_after_amendment_resolution({"resolution_outcome": "CUSTOMER_CORRECT"})
        == "waiting_approval_node"
    )
    assert (
        route_after_amendment_resolution({"resolution_outcome": "COMPANY_CORRECT"})
        == "mail_agent_node"
    )
    assert (
        route_after_waiting_approval(
            {
                "dispute_category": "AMENDMENT",
                "resolution_outcome": "CUSTOMER_CORRECT",
            }
        )
        == "apply_amendment_node"
    )
    assert (
        route_after_waiting_approval(
            {
                "dispute_category": "AMENDMENT",
                "resolution_outcome": "COMPANY_CORRECT",
            }
        )
        == "mail_agent_node"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        ("APPROVE", "CUSTOMER_CORRECT"),
        ("REJECT", "COMPANY_CORRECT"),
    ],
)
async def test_amendment_approval_semantics_regression(
    db_session: AsyncSession,
    decision: str,
    expected: str,
):
    _, dispute = await _create_dispute(
        db_session,
        category="AMENDMENT",
        status="WAITING_ASSOCIATE_APPROVAL",
    )

    result = await waiting_approval_node(
        {
            "dispute_id": dispute.id,
            "dispute_category": "AMENDMENT",
            "resolution_outcome": decision,
            "metadata": {},
        },
        _config(db_session, dispute.id),
    )

    assert result["resolution_outcome"] == expected
    await db_session.refresh(dispute)
    assert dispute.resolution_outcome == expected
