from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from main import app
from src.core.config.settings import settings
from src.core.exceptions.business_exceptions import ValidationException
from src.core.security.dependencies import get_current_user
from src.core.services.audit_service import AuditService
from src.core.services.dispute_close_service import DisputeCloseService
from src.core.services.dispute_decision_service import DisputeDecisionService
from src.core.services.escalation_service import EscalationService
from src.core.services.recommendation_service import RecommendationService
from src.core.services.sla_service import SLAService
from src.core.services.workflow_context_service import WorkflowContextService
from src.core.workflow.resume_service import DisputeResumeService
from src.data.repositories import (
    ActivityRepository,
    CaseRepository,
    CommentRepository,
    DisputeRepository,
    EscalationRepository,
    RecommendationRepository,
    SLARepository,
    WorkflowContextRepository,
)
from src.schemas.auth import RoleName, TokenPayload

MOCK_ASSOCIATE = TokenPayload(
    sub=UUID("00000000-0000-0000-0000-000000000102"),
    email="associate@paisavasool.com",
    role=RoleName.FINANCE_ASSOCIATE,
    is_active=True,
)

MOCK_MANAGER = TokenPayload(
    sub=UUID("00000000-0000-0000-0000-000000000101"),
    email="manager@paisavasool.com",
    role=RoleName.FINANCE_MANAGER,
    is_active=True,
)


def _build_close_service(db_session: AsyncSession) -> DisputeCloseService:
    dispute_repo = DisputeRepository(db_session)
    sla_repo = SLARepository(db_session)
    activity_repo = ActivityRepository(db_session)
    audit_service = AuditService(activity_repo)
    escalation_repo = EscalationRepository(db_session)
    escalation_service = EscalationService(
        escalation_repo, sla_repo, dispute_repo, audit_service
    )
    workflow_context_repo = WorkflowContextRepository(db_session)
    workflow_context_service = WorkflowContextService(
        workflow_context_repo, audit_service
    )
    comment_repo = CommentRepository(db_session)
    ar_client = MagicMock()
    ar_client.resume_collections = AsyncMock(return_value={})

    return DisputeCloseService(
        dispute_repo=dispute_repo,
        sla_repo=sla_repo,
        audit_service=audit_service,
        ar_client=ar_client,
        escalation_service=escalation_service,
        workflow_context_service=workflow_context_service,
        workflow_context_repo=workflow_context_repo,
        comment_repo=comment_repo,
    )


async def _seed_dispute(
    db_session: AsyncSession,
    *,
    status: str = "OPEN",
    with_sla: bool = True,
    with_context: bool = True,
    assigned_to: UUID | None = None,
    manager_id: UUID | None = None,
) -> tuple:
    case_repo = CaseRepository(db_session)
    dispute_repo = DisputeRepository(db_session)
    sla_repo = SLARepository(db_session)
    activity_repo = ActivityRepository(db_session)
    context_repo = WorkflowContextRepository(db_session)

    case = await case_repo.create_case(
        case_number=f"CASE-{uuid4().hex[:6].upper()}",
        customer_email="close-test@example.com",
    )
    dispute = await dispute_repo.create_dispute(
        dispute_number=f"DISP-{uuid4().hex[:6].upper()}",
        case_id=case.id,
        invoice_id=uuid4(),
        invoice_number="INV-CLOSE-01",
        customer_id=uuid4(),
        dispute_category="AMENDMENT",
        status=status,
        assigned_to=assigned_to,
        manager_id=manager_id,
    )

    if with_sla:
        audit_service = AuditService(activity_repo)
        sla_service = SLAService(sla_repo, dispute_repo, audit_service, settings)
        await sla_service.create_sla(dispute.id)

    if with_context:
        await context_repo.create_workflow_context(
            dispute_id=dispute.id,
            workflow_name="dispute_resolution",
            current_node="waiting_approval_node",
            workflow_state={"workflow_status": status},
        )

    return dispute, case


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    ["OPEN", "WAITING_CUSTOMER", "WAITING_ASSOCIATE_APPROVAL", "ESCALATED"],
)
async def test_manual_close_from_allowed_statuses(
    db_session: AsyncSession, status: str
):
    close_service = _build_close_service(db_session)
    dispute, _ = await _seed_dispute(db_session, status=status)
    performed_by = uuid4()

    result = await close_service.manual_close(
        dispute.id,
        resolution_method="PHONE",
        resolution_outcome="CUSTOMER_CORRECT",
        comments="Resolved by phone call.",
        performed_by=performed_by,
    )

    assert result.status == "CLOSED"
    assert result.resolution_outcome == "CUSTOMER_CORRECT"
    assert result.closed_at is not None
    assert result.resolved_at is not None

    sla_repo = SLARepository(db_session)
    sla = await sla_repo.get_by_dispute_id(dispute.id)
    assert sla is not None
    assert sla.status == "CLOSED"

    close_service.ar_client.resume_collections.assert_awaited_once_with(
        dispute.invoice_id
    )


@pytest.mark.asyncio
async def test_manual_close_stores_resolution_method_in_audit_and_comment(
    db_session: AsyncSession,
):
    close_service = _build_close_service(db_session)
    dispute, _ = await _seed_dispute(db_session, status="OPEN")
    performed_by = uuid4()

    await close_service.manual_close(
        dispute.id,
        resolution_method="PHONE",
        resolution_outcome="CUSTOMER_CORRECT",
        comments="Agreed on credit note.",
        performed_by=performed_by,
    )

    activity_repo = ActivityRepository(db_session)
    activities = await activity_repo.list_activities_for_dispute(dispute.id)
    manual_close = next(a for a in activities if a.activity_type == "MANUAL_CLOSE")
    assert manual_close.activity_metadata["resolution_method"] == "PHONE"
    assert manual_close.activity_metadata["outcome"] == "CUSTOMER_CORRECT"

    comment_repo = CommentRepository(db_session)
    comments = await comment_repo.list_comments_for_dispute(dispute.id)
    assert any("phone call" in c.comment.lower() for c in comments)
    assert any("Customer correct" in c.comment for c in comments)


@pytest.mark.asyncio
async def test_manual_close_idempotent_when_already_closed(db_session: AsyncSession):
    close_service = _build_close_service(db_session)
    dispute, _ = await _seed_dispute(db_session, status="CLOSED")

    result = await close_service.manual_close(
        dispute.id,
        resolution_method="EMAIL",
        resolution_outcome="COMPANY_CORRECT",
        comments="Should be ignored.",
        performed_by=uuid4(),
    )

    assert result.status == "CLOSED"
    close_service.ar_client.resume_collections.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["FAILED", "CANCELLED"])
async def test_manual_close_rejects_terminal_statuses(
    db_session: AsyncSession, status: str
):
    close_service = _build_close_service(db_session)
    dispute, _ = await _seed_dispute(db_session, status=status)

    with pytest.raises(
        ValidationException, match=f"Cannot close dispute in status {status}"
    ):
        await close_service.manual_close(
            dispute.id,
            resolution_method="PHONE",
            resolution_outcome="CUSTOMER_CORRECT",
            comments="Should fail.",
            performed_by=uuid4(),
        )


@pytest.mark.asyncio
async def test_resume_blocked_after_manual_close(db_session: AsyncSession):
    close_service = _build_close_service(db_session)
    dispute, _ = await _seed_dispute(db_session, status="WAITING_ASSOCIATE_APPROVAL")

    await close_service.manual_close(
        dispute.id,
        resolution_method="IN_PERSON",
        resolution_outcome="COMPANY_CORRECT",
        comments="Closed offline.",
        performed_by=uuid4(),
    )

    resume_service = DisputeResumeService()
    with pytest.raises(ValidationException, match="Cannot resume workflow"):
        await resume_service.resume_workflow(
            db=db_session,
            dispute_id=dispute.id,
            state_updates={"resolution_outcome": "APPROVE"},
            as_node="waiting_approval_node",
        )


@pytest.mark.asyncio
async def test_decision_blocked_after_manual_close(db_session: AsyncSession):
    close_service = _build_close_service(db_session)
    dispute, _ = await _seed_dispute(db_session, status="OPEN")

    await close_service.manual_close(
        dispute.id,
        resolution_method="OTHER",
        resolution_outcome="CUSTOMER_CORRECT",
        comments="Closed offline.",
        performed_by=uuid4(),
    )

    dispute_repo = DisputeRepository(db_session)
    comment_repo = CommentRepository(db_session)
    activity_repo = ActivityRepository(db_session)
    recommendation_repo = RecommendationRepository(db_session)
    audit_service = AuditService(activity_repo)
    recommendation_service = RecommendationService(recommendation_repo, audit_service)
    decision_service = DisputeDecisionService(
        dispute_repo,
        comment_repo,
        activity_repo,
        recommendation_service,
        DisputeResumeService(),
    )

    with pytest.raises(ValidationException, match="Cannot submit decision"):
        await decision_service.submit_associate_decision(
            dispute.id,
            decision="APPROVE",
            comments="Too late.",
            amended_invoice_json=None,
            performed_by=uuid4(),
        )


@pytest.mark.asyncio
async def test_close_dispute_api_endpoint(
    mock_client: AsyncClient, db_session, seed_users
):
    dispute, _ = await _seed_dispute(db_session, status="WAITING_CUSTOMER")
    app.dependency_overrides[get_current_user] = lambda: MOCK_ASSOCIATE

    payload = {
        "resolution_method": "PHONE",
        "resolution_outcome": "CUSTOMER_CORRECT",
        "comments": "Customer confirmed payment over the phone.",
    }
    resp = await mock_client.post(
        f"/api/v1/disputes/{dispute.id}/close",
        json=payload,
        headers={"Authorization": "Bearer dummy"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "CLOSED"
    assert data["resolution_outcome"] == "CUSTOMER_CORRECT"


@pytest.mark.asyncio
async def test_close_dispute_api_idempotent(
    mock_client: AsyncClient, db_session, seed_users
):
    dispute, _ = await _seed_dispute(db_session, status="CLOSED")
    app.dependency_overrides[get_current_user] = lambda: MOCK_ASSOCIATE

    payload = {
        "resolution_method": "EMAIL",
        "resolution_outcome": "COMPANY_CORRECT",
        "comments": "Second close attempt.",
    }
    resp = await mock_client.post(
        f"/api/v1/disputes/{dispute.id}/close",
        json=payload,
        headers={"Authorization": "Bearer dummy"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "CLOSED"


@pytest.mark.asyncio
async def test_close_dispute_api_rejects_failed(
    mock_client: AsyncClient, db_session, seed_users
):
    dispute, _ = await _seed_dispute(db_session, status="FAILED")
    app.dependency_overrides[get_current_user] = lambda: MOCK_ASSOCIATE

    payload = {
        "resolution_method": "PHONE",
        "resolution_outcome": "CUSTOMER_CORRECT",
        "comments": "Should not close.",
    }
    resp = await mock_client.post(
        f"/api/v1/disputes/{dispute.id}/close",
        json=payload,
        headers={"Authorization": "Bearer dummy"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_associate_decision_api_blocked_after_close(
    mock_client: AsyncClient, db_session, seed_users
):
    dispute, _ = await _seed_dispute(db_session, status="WAITING_ASSOCIATE_APPROVAL")
    app.dependency_overrides[get_current_user] = lambda: MOCK_ASSOCIATE

    close_resp = await mock_client.post(
        f"/api/v1/disputes/{dispute.id}/close",
        json={
            "resolution_method": "IN_PERSON",
            "resolution_outcome": "COMPANY_CORRECT",
            "comments": "Manual resolution.",
        },
        headers={"Authorization": "Bearer dummy"},
    )
    assert close_resp.status_code == 200

    decision_resp = await mock_client.post(
        f"/api/v1/disputes/{dispute.id}/associate-decision",
        json={"decision": "APPROVE", "comments": "Too late."},
        headers={"Authorization": "Bearer dummy"},
    )
    assert decision_resp.status_code == 400


@pytest.mark.asyncio
async def test_associate_close_blocked_when_escalated(
    mock_client: AsyncClient, db_session, seed_users
):
    dispute, _ = await _seed_dispute(
        db_session,
        status="ESCALATED",
        assigned_to=seed_users["associate"].id,
        manager_id=seed_users["manager"].id,
    )
    app.dependency_overrides[get_current_user] = lambda: MOCK_ASSOCIATE

    resp = await mock_client.post(
        f"/api/v1/disputes/{dispute.id}/close",
        json={
            "resolution_method": "PHONE",
            "resolution_outcome": "CUSTOMER_CORRECT",
            "comments": "Associate should not close after escalation.",
        },
        headers={"Authorization": "Bearer dummy"},
    )
    assert resp.status_code == 403
    assert "escalated" in resp.json()["error"]["message"].lower()


@pytest.mark.asyncio
async def test_associate_decision_blocked_when_escalated(
    mock_client: AsyncClient, db_session, seed_users
):
    dispute, _ = await _seed_dispute(
        db_session,
        status="ESCALATED",
        assigned_to=seed_users["associate"].id,
        manager_id=seed_users["manager"].id,
    )
    app.dependency_overrides[get_current_user] = lambda: MOCK_ASSOCIATE

    resp = await mock_client.post(
        f"/api/v1/disputes/{dispute.id}/associate-decision",
        json={"decision": "APPROVE", "comments": "Should be frozen."},
        headers={"Authorization": "Bearer dummy"},
    )
    assert resp.status_code == 403
    assert "escalated" in resp.json()["error"]["message"].lower()


@pytest.mark.asyncio
async def test_manager_close_allowed_when_escalated(
    mock_client: AsyncClient, db_session, seed_users
):
    dispute, _ = await _seed_dispute(
        db_session,
        status="ESCALATED",
        assigned_to=seed_users["associate"].id,
        manager_id=seed_users["manager"].id,
    )
    app.dependency_overrides[get_current_user] = lambda: MOCK_MANAGER

    resp = await mock_client.post(
        f"/api/v1/disputes/{dispute.id}/close",
        json={
            "resolution_method": "PHONE",
            "resolution_outcome": "CUSTOMER_CORRECT",
            "comments": "Manager resolving escalated dispute.",
        },
        headers={"Authorization": "Bearer dummy"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "CLOSED"
