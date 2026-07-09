import base64
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from main import app
from src.core.config.settings import settings
from src.core.security.dependencies import get_current_user
from src.core.services.case_attachment_service import CaseAttachmentService
from src.core.services.case_intake_service import CaseIntakeService
from src.data.models.postgres.case_attachment import CaseAttachment
from src.data.repositories import (
    ActivityRepository,
    CaseAttachmentRepository,
    CaseRepository,
    DisputeRepository,
)
from src.schemas.auth import RoleName, TokenPayload
from src.schemas.case import CaseAttachmentDTO, CaseIntakeRequest

MOCK_MANAGER = TokenPayload(
    sub=UUID("00000000-0000-0000-0000-000000000101"),
    email="manager@paisavasool.com",
    role=RoleName.FINANCE_MANAGER,
    is_active=True,
)

SAMPLE_PDF_BYTES = b"%PDF-1.4 test attachment content"
SAMPLE_PDF_B64 = base64.b64encode(SAMPLE_PDF_BYTES).decode("ascii")
RAW_CONTENT_WITH_ATTACHMENT = (
    "SUBJECT: Dispute\n"
    "EMAIL BODY: The invoice 2599 has quantity wrong\n"
    "ATTACHMENT CONTENT:\n"
    "ATTACHMENT 1 (Purchase_Order.pdf): extracted text"
)


@pytest.fixture
def send_attachment_storage_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    storage_dir = tmp_path / "send-attachments"
    storage_dir.mkdir()
    monkeypatch.setattr(settings, "SEND_ATTACHMENT_STORAGE_DIR", str(storage_dir))
    return storage_dir


@pytest.fixture
def attachment_storage_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    storage_dir = tmp_path / "case-attachments"
    storage_dir.mkdir()
    monkeypatch.setattr(settings, "CASE_ATTACHMENT_STORAGE_DIR", str(storage_dir))
    return storage_dir


def _build_intake_service(db_session: AsyncSession) -> CaseIntakeService:
    case_repo = CaseRepository(db_session)
    dispute_repo = DisputeRepository(db_session)
    activity_repo = ActivityRepository(db_session)
    attachment_repo = CaseAttachmentRepository(db_session)
    attachment_service = CaseAttachmentService(case_repo, attachment_repo)
    return CaseIntakeService(case_repo, dispute_repo, activity_repo, attachment_service)


@pytest.mark.asyncio
async def test_intake_persists_attachments_without_altering_raw_content(
    db_session: AsyncSession,
    attachment_storage_dir: Path,
):
    """Intake with base64 attachments stores files and DB rows; raw_content is unchanged."""
    intake_service = _build_intake_service(db_session)
    message_id = f"msg-{uuid4().hex}"
    payload = CaseIntakeRequest(
        customer_email="customer@example.com",
        email_subject="Dispute",
        email_body="The invoice 2599 has quantity wrong",
        raw_content=RAW_CONTENT_WITH_ATTACHMENT,
        message_id=message_id,
        attachments=[
            {
                "filename": "Purchase_Order_PO-2599.pdf",
                "mime_type": "application/pdf",
                "content_base64": SAMPLE_PDF_B64,
            }
        ],
    )

    with patch("src.core.services.case_intake_service.process_dispute_case.delay"):
        result = await intake_service.process_intake(
            payload,
            performed_by=MOCK_MANAGER.sub,
        )

    assert result["status"] == "QUEUED"
    case_id = UUID(str(result["case_id"]))

    case_repo = CaseRepository(db_session)
    case = await case_repo.get_by_id(case_id)
    assert case is not None
    assert case.raw_content == RAW_CONTENT_WITH_ATTACHMENT

    attachments = await db_session.scalars(
        select(CaseAttachment).where(CaseAttachment.case_id == case_id)
    )
    saved = attachments.all()
    assert len(saved) == 1
    assert saved[0].filename == "Purchase_Order_PO-2599.pdf"
    assert saved[0].mime_type == "application/pdf"

    disk_path = attachment_storage_dir / saved[0].file_path
    assert disk_path.is_file()
    assert disk_path.read_bytes() == SAMPLE_PDF_BYTES


@pytest.mark.asyncio
async def test_list_and_download_case_attachments(
    mock_client: AsyncClient,
    db_session: AsyncSession,
    attachment_storage_dir: Path,
):
    """List and file endpoints return attachment metadata and PDF bytes."""
    app.dependency_overrides[get_current_user] = lambda: MOCK_MANAGER
    headers = {"Authorization": "Bearer dummy"}

    with patch("src.core.services.case_intake_service.process_dispute_case.delay"):
        intake_resp = await mock_client.post(
            "/api/v1/cases/intake",
            json={
                "customer_email": "customer@example.com",
                "email_subject": "Dispute",
                "email_body": "Please review the attached PO.",
                "raw_content": RAW_CONTENT_WITH_ATTACHMENT,
                "message_id": f"msg-{uuid4().hex}",
                "attachments": [
                    {
                        "filename": "Purchase_Order_PO-2599.pdf",
                        "mime_type": "application/pdf",
                        "content_base64": SAMPLE_PDF_B64,
                    }
                ],
            },
            headers=headers,
        )

    assert intake_resp.status_code == 201
    case_id = intake_resp.json()["case_id"]

    list_resp = await mock_client.get(
        f"/api/v1/cases/{case_id}/attachments",
        headers=headers,
    )
    assert list_resp.status_code == 200
    attachments = list_resp.json()
    assert len(attachments) == 1
    assert attachments[0]["filename"] == "Purchase_Order_PO-2599.pdf"
    assert attachments[0]["mime_type"] == "application/pdf"

    attachment_id = attachments[0]["id"]
    file_resp = await mock_client.get(
        f"/api/v1/cases/{case_id}/attachments/{attachment_id}/file",
        headers=headers,
    )
    assert file_resp.status_code == 200
    assert file_resp.headers["content-type"].startswith("application/pdf")
    assert file_resp.content == SAMPLE_PDF_BYTES


@pytest.mark.asyncio
async def test_merge_attachments_to_correlated_dispute_case(
    db_session: AsyncSession,
    attachment_storage_dir: Path,
):
    """Follow-up intake attachments are copied onto the primary dispute case."""
    intake_service = _build_intake_service(db_session)
    attachment_repo = CaseAttachmentRepository(db_session)
    case_repo = CaseRepository(db_session)
    attachment_service = CaseAttachmentService(case_repo, attachment_repo)

    primary_case_id = uuid4()
    follow_up_case_id = uuid4()

    primary_case = await case_repo.create_case(
        case_number="CASE-TEST-001",
        customer_email="customer@example.com",
        email_subject="Dispute",
        email_body="invoice 2799 has wrong quantity",
        original_message_id=f"msg-{uuid4().hex}",
        raw_content="invoice 2799 has wrong quantity",
    )
    primary_case_id = primary_case.id

    follow_up_payload = CaseIntakeRequest(
        customer_email="customer@example.com",
        email_subject="Dispute",
        email_body="the po for the inv 2799",
        raw_content=RAW_CONTENT_WITH_ATTACHMENT,
        message_id=f"msg-{uuid4().hex}",
        attachments=[
            {
                "filename": "Purchase_Order_2799.pdf",
                "mime_type": "application/pdf",
                "content_base64": SAMPLE_PDF_B64,
            }
        ],
    )

    with patch("src.core.services.case_intake_service.process_dispute_case.delay"):
        follow_up_result = await intake_service.process_intake(
            follow_up_payload,
            performed_by=MOCK_MANAGER.sub,
        )

    follow_up_case_id = UUID(str(follow_up_result["case_id"]))
    await db_session.commit()

    merged = await attachment_service.merge_attachments_to_case(
        follow_up_case_id, primary_case_id
    )
    await db_session.commit()

    assert len(merged) == 1
    assert merged[0].case_id == primary_case_id
    assert merged[0].filename == "Purchase_Order_2799.pdf"

    primary_attachments = await attachment_repo.list_by_case_id(primary_case_id)
    assert len(primary_attachments) == 1
    disk_path = attachment_storage_dir / primary_attachments[0].file_path
    assert disk_path.is_file()
    assert disk_path.read_bytes() == SAMPLE_PDF_BYTES


@pytest.mark.asyncio
async def test_persist_send_attachments_stores_in_send_storage(
    db_session: AsyncSession,
    attachment_storage_dir: Path,
    send_attachment_storage_dir: Path,
):
    """Outbound compose-reply PDFs are stored under send-attachments and are downloadable."""
    case_repo = CaseRepository(db_session)
    attachment_repo = CaseAttachmentRepository(db_session)
    attachment_service = CaseAttachmentService(case_repo, attachment_repo)

    case = await case_repo.create_case(
        case_number="CASE-SEND-001",
        customer_email="customer@example.com",
        email_subject="Dispute",
        email_body="Please review the attached document.",
        original_message_id=f"msg-{uuid4().hex}",
        raw_content="Please review the attached document.",
    )
    await db_session.commit()

    saved = await attachment_service.persist_send_attachments(
        case.id,
        [
            CaseAttachmentDTO(
                filename="Outbound_Report.pdf",
                mime_type="application/pdf",
                content_base64=SAMPLE_PDF_B64,
            )
        ],
    )
    await db_session.commit()

    assert len(saved) == 1
    assert saved[0].filename == "Outbound_Report.pdf"
    assert saved[0].file_path.startswith("send-attachments/")

    disk_path = send_attachment_storage_dir / saved[0].file_path.removeprefix(
        "send-attachments/"
    )
    assert disk_path.is_file()
    assert disk_path.read_bytes() == SAMPLE_PDF_BYTES

    file_bytes, attachment = await attachment_service.get_attachment_file(
        case.id, saved[0].id
    )
    assert attachment.filename == "Outbound_Report.pdf"
    assert file_bytes == SAMPLE_PDF_BYTES
