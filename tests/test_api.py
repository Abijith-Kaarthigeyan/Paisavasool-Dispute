from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient

from main import app
from src.core.security.dependencies import get_current_user
from src.data.repositories import (
    ActivityRepository,
    CaseRepository,
    CommentRepository,
    DisputeRepository,
    RecommendationRepository,
    ReviewQueueRepository,
    WorkflowContextRepository,
)
from src.schemas.auth import RoleName, TokenPayload

# Define mock users for dependency overrides
MOCK_MANAGER = TokenPayload(
    sub=UUID("00000000-0000-0000-0000-000000000101"),
    email="manager@paisavasool.com",
    role=RoleName.FINANCE_MANAGER,
    is_active=True,
)

MOCK_ASSOCIATE = TokenPayload(
    sub=UUID("00000000-0000-0000-0000-000000000102"),
    email="associate@paisavasool.com",
    role=RoleName.FINANCE_ASSOCIATE,
    is_active=True,
)


@pytest.fixture
async def seed_api_data(db_session, seed_users):
    case_repo = CaseRepository(db_session)
    dispute_repo = DisputeRepository(db_session)
    comment_repo = CommentRepository(db_session)
    activity_repo = ActivityRepository(db_session)
    rec_repo = RecommendationRepository(db_session)
    context_repo = WorkflowContextRepository(db_session)
    review_repo = ReviewQueueRepository(db_session)

    # 1. Create Case
    case = await case_repo.create_case(
        case_number="CASE-API-01",
        customer_email="api@example.com",
        email_subject="API subject",
        email_body="API body",
    )

    # 2. Create Dispute
    dispute = await dispute_repo.create_dispute(
        dispute_number="DISP-API-01",
        case_id=case.id,
        invoice_id=uuid4(),
        invoice_number="INV-API-01",
        customer_id=uuid4(),
        dispute_category="AMENDMENT",
        status="OPEN",
        assigned_to=seed_users["associate"].id,
        manager_id=seed_users["manager"].id,
    )

    # 3. Add activity & comment & recommendation & context
    activity = await activity_repo.create_activity(
        dispute_id=dispute.id,
        activity_type="DISPUTE_CREATED",
    )
    comment = await comment_repo.create_comment(
        dispute_id=dispute.id,
        comment="Initial comment",
        comment_type="INTERNAL",
        created_by=MOCK_ASSOCIATE.sub,
    )
    rec = await rec_repo.create_recommendation(
        dispute_id=dispute.id,
        recommended_action="APPLY_CREDIT",
        confidence=0.95,
        created_by_agent="TriageAgent",
    )
    context = await context_repo.create_workflow_context(
        dispute_id=dispute.id,
        workflow_name="dispute_resolution",
        current_node="triage_node",
        workflow_state={"data": "test"},
    )
    review_item = await review_repo.create_review_queue_item(
        dispute_id=dispute.id,
        review_reason="AI_LOW_CONFIDENCE",
        status="PENDING",
    )

    return {
        "case": case,
        "dispute": dispute,
        "activity": activity,
        "comment": comment,
        "recommendation": rec,
        "context": context,
        "review_item": review_item,
    }


@pytest.mark.asyncio
async def test_get_cases_endpoint(mock_client: AsyncClient, seed_api_data):
    # Override current user to manager
    app.dependency_overrides[get_current_user] = lambda: MOCK_MANAGER

    resp = await mock_client.get(
        "/api/v1/cases", headers={"Authorization": "Bearer dummy"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["case_number"] == "CASE-API-01"


@pytest.mark.asyncio
async def test_get_case_by_id_endpoint(mock_client: AsyncClient, seed_api_data):
    app.dependency_overrides[get_current_user] = lambda: MOCK_MANAGER
    case_id = seed_api_data["case"].id

    resp = await mock_client.get(
        f"/api/v1/cases/{case_id}", headers={"Authorization": "Bearer dummy"}
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == str(case_id)


@pytest.mark.asyncio
async def test_get_disputes_endpoint(mock_client: AsyncClient, seed_api_data):
    app.dependency_overrides[get_current_user] = lambda: MOCK_ASSOCIATE

    resp = await mock_client.get(
        "/api/v1/disputes", headers={"Authorization": "Bearer dummy"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["dispute_number"] == "DISP-API-01"


@pytest.mark.asyncio
async def test_get_dispute_by_id_endpoint(mock_client: AsyncClient, seed_api_data):
    app.dependency_overrides[get_current_user] = lambda: MOCK_ASSOCIATE
    dispute_id = seed_api_data["dispute"].id

    resp = await mock_client.get(
        f"/api/v1/disputes/{dispute_id}", headers={"Authorization": "Bearer dummy"}
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == str(dispute_id)


@pytest.mark.asyncio
async def test_get_dispute_activities_endpoint(mock_client: AsyncClient, seed_api_data):
    app.dependency_overrides[get_current_user] = lambda: MOCK_ASSOCIATE
    dispute_id = seed_api_data["dispute"].id

    resp = await mock_client.get(
        f"/api/v1/disputes/{dispute_id}/activities",
        headers={"Authorization": "Bearer dummy"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["activity_type"] == "DISPUTE_CREATED"


@pytest.mark.asyncio
async def test_get_dispute_comments_endpoint(mock_client: AsyncClient, seed_api_data):
    app.dependency_overrides[get_current_user] = lambda: MOCK_ASSOCIATE
    dispute_id = seed_api_data["dispute"].id

    resp = await mock_client.get(
        f"/api/v1/disputes/{dispute_id}/comments",
        headers={"Authorization": "Bearer dummy"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["comment"] == "Initial comment"


@pytest.mark.asyncio
async def test_create_dispute_comment_endpoint(mock_client: AsyncClient, seed_api_data):
    app.dependency_overrides[get_current_user] = lambda: MOCK_ASSOCIATE
    dispute_id = seed_api_data["dispute"].id

    payload = {"comment": "New API comment", "comment_type": "INTERNAL"}
    resp = await mock_client.post(
        f"/api/v1/disputes/{dispute_id}/comments",
        json=payload,
        headers={"Authorization": "Bearer dummy"},
    )
    assert resp.status_code == 200
    assert resp.json()["comment"] == "New API comment"


@pytest.mark.asyncio
async def test_reassign_dispute_endpoint(
    mock_client: AsyncClient, seed_api_data, seed_users
):
    app.dependency_overrides[get_current_user] = lambda: MOCK_MANAGER
    dispute_id = seed_api_data["dispute"].id
    assoc_id = seed_users["associate"].id

    payload = {"assigned_to": str(assoc_id)}
    resp = await mock_client.post(
        f"/api/v1/disputes/{dispute_id}/reassign",
        json=payload,
        headers={"Authorization": "Bearer dummy"},
    )
    assert resp.status_code == 200
    assert resp.json()["assigned_to"] == str(assoc_id)


@pytest.mark.asyncio
async def test_get_recommendations_endpoint(mock_client: AsyncClient, seed_api_data):
    app.dependency_overrides[get_current_user] = lambda: MOCK_ASSOCIATE
    dispute_id = seed_api_data["dispute"].id

    resp = await mock_client.get(
        f"/api/v1/disputes/{dispute_id}/recommendations",
        headers={"Authorization": "Bearer dummy"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["recommended_action"] == "APPLY_CREDIT"


@pytest.mark.asyncio
async def test_get_review_queue_endpoint(mock_client: AsyncClient, seed_api_data):
    app.dependency_overrides[get_current_user] = lambda: MOCK_MANAGER

    resp = await mock_client.get(
        "/api/v1/review-queue", headers={"Authorization": "Bearer dummy"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["review_reason"] == "AI_LOW_CONFIDENCE"


@pytest.mark.asyncio
async def test_resume_dispute_endpoint(mock_client: AsyncClient, seed_api_data):
    app.dependency_overrides[get_current_user] = lambda: MOCK_ASSOCIATE
    dispute_id = seed_api_data["dispute"].id

    resp = await mock_client.post(
        f"/api/v1/disputes/{dispute_id}/resume",
        headers={"Authorization": "Bearer dummy"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCESS"
    assert data["context"]["workflow_name"] == "dispute_resolution"
