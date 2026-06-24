from datetime import datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_case_repository, get_dispute_repository
from src.core.security.dependencies import require_finance
from src.data.clients.postgres_client import get_async_db
from src.data.repositories.case_repository import CaseRepository
from src.data.repositories.dispute_repository import DisputeRepository
from src.schemas.auth import TokenPayload
from src.schemas.case import CaseResponse, CaseIntakeRequest
from src.schemas.dispute import DisputeResponse

router = APIRouter(prefix="/cases", tags=["Case Management"])


@router.get("", response_model=list[CaseResponse])
async def list_cases(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    current_user: TokenPayload = Depends(require_finance),
    case_repo: CaseRepository = Depends(get_case_repository),
):
    """Retrieves a paginated list of cases."""
    cases = await case_repo.list_cases(limit=limit, offset=offset)
    return [CaseResponse.model_validate(c) for c in cases]


@router.post("/intake", status_code=201)
async def create_case_intake(
    payload: CaseIntakeRequest,
    current_user: TokenPayload = Depends(require_finance),
    case_repo: CaseRepository = Depends(get_case_repository),
    db: AsyncSession = Depends(get_async_db),
):
    """Processes customer email intake, checks idempotency, and queues triage/generation workflow."""
    from sqlalchemy import select, text
    from src.infrastructure.celery.tasks import process_dispute_case
    from src.observability.logging.logger import logger

    message_id = payload.message_id

    # 1. Check Idempotency by message_id
    if message_id:
        raw_res = await db.execute(
            text("SELECT id, case_number FROM dispute_cases WHERE original_message_id = :msg_id AND is_deleted = false"),
            {"msg_id": message_id}
        )
        row = raw_res.first()
        if row:
            case_id, case_number = row[0], row[1]
            logger.info("Idempotency triggered for intake message_id: %s. Reusing Case %s", message_id, case_id)
            
            # Log idempotency audit record for all associated disputes
            disp_res = await db.execute(
                text("SELECT id FROM disputes WHERE case_id = :c_id AND is_deleted = false"),
                {"c_id": case_id}
            )
            dispute_ids = [r[0] for r in disp_res.fetchall()]
            for d_id in dispute_ids:
                await db.execute(
                    text("INSERT INTO dispute_activities (id, dispute_id, activity_type, activity_metadata, performed_by, created_at) "
                         "VALUES (:act_id, :d_id, 'INTAKE_IDEMPOTENCY', :meta, :user_id, NOW())"),
                    {"act_id": uuid4(), "d_id": d_id, "meta": '{"info": "Intake email with message_id was resubmitted and bypassed"}', "user_id": current_user.sub}
                )
            await db.commit()
            
            return {"case_id": case_id, "case_number": case_number, "status": "DUPLICATE_BYPASS"}

    # 2. Create the Dispute Case
    year = datetime.now().year
    count_res = await db.execute(text("SELECT count(*) FROM dispute_cases"))
    count = count_res.scalar() or 0
    case_number = f"CASE-{year}-{count + 1:06d}"

    case = await case_repo.create_case(
        case_number=case_number,
        customer_email=payload.customer_email,
        email_subject=payload.email_subject,
        email_body=payload.email_body,
        original_message_id=message_id,
        raw_content=payload.raw_content,
    )
    await db.commit()

    # 3. Queue Celery process_dispute_case workflow task
    process_dispute_case.delay(str(case.id))

    return {
        "case_id": case.id,
        "case_number": case.case_number,
        "status": "QUEUED",
    }


@router.get("/{id}/disputes", response_model=list[DisputeResponse])
async def get_case_disputes(
    id: UUID,
    current_user: TokenPayload = Depends(require_finance),
    dispute_repo: DisputeRepository = Depends(get_dispute_repository),
    db: AsyncSession = Depends(get_async_db),
):
    """Retrieves all disputes associated with a specific case."""
    from sqlalchemy import select
    from src.data.models.postgres.dispute import Dispute
    result = await db.execute(
        select(Dispute).where(Dispute.case_id == id, Dispute.is_deleted.is_(False))
    )
    disputes = result.scalars().all()
    return [DisputeResponse.model_validate(d) for d in disputes]


@router.get("/{id}", response_model=CaseResponse)
async def get_case(
    id: UUID,
    current_user: TokenPayload = Depends(require_finance),
    case_repo: CaseRepository = Depends(get_case_repository),
):
    """Fetches details of a specific case by ID."""
    case = await case_repo.get_by_id(id)
    if not case:
        raise HTTPException(status_code=404, detail=f"Case {id} not found.")
    return CaseResponse.model_validate(case)
