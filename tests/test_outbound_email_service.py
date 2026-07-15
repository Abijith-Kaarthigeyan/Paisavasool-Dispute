"""Unit tests for OutboundEmailService and ARServiceClient.send_email."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.core.exceptions.business_exceptions import ARServiceClientException
from src.core.services.outbound_email_service import OutboundEmailService
from src.data.clients.ar_service_client import ARServiceClient


@pytest.mark.asyncio
async def test_outbound_email_service_updates_communication_on_success():
    ar_client = AsyncMock()
    ar_client.send_email.return_value = {
        "gmail_message_id": "gmail-msg-1",
        "gmail_thread_id": "gmail-thread-1",
        "rfc_message_id": "<gmail-msg-1@gmail.com>",
        "sent": True,
    }

    comm_repo = AsyncMock()
    comm_repo.list_customer_communications_chronological.return_value = []
    case_repo = AsyncMock()
    audit_service = AsyncMock()

    service = OutboundEmailService(
        ar_client=ar_client,
        comm_repo=comm_repo,
        case_repo=case_repo,
        audit_service=audit_service,
    )

    dispute_id = uuid4()
    communication = MagicMock()
    communication.id = uuid4()
    communication.recipient = "customer@example.com"
    communication.subject = "Update"
    communication.body = "Hello"

    case = MagicMock()
    case.gmail_thread_id = "gmail-thread-1"
    case.rfc_message_id = "<original@gmail.com>"

    await service.send_communication(
        dispute_id=dispute_id,
        communication=communication,
        case=case,
        use_thread=True,
    )

    ar_client.send_email.assert_awaited_once()
    payload = ar_client.send_email.await_args.args[0]
    assert payload["thread_id"] == "gmail-thread-1"
    assert payload["in_reply_to"] == "<original@gmail.com>"
    assert "<original@gmail.com>" in payload["references"]

    comm_repo.update_thread_metadata.assert_awaited_once()
    audit_service.log_event.assert_not_called()


@pytest.mark.asyncio
async def test_outbound_email_service_logs_audit_on_failure():
    ar_client = AsyncMock()
    ar_client.send_email.side_effect = ARServiceClientException("send failed")

    comm_repo = AsyncMock()
    case_repo = AsyncMock()
    audit_service = AsyncMock()

    service = OutboundEmailService(
        ar_client=ar_client,
        comm_repo=comm_repo,
        case_repo=case_repo,
        audit_service=audit_service,
    )

    communication = MagicMock()
    communication.id = uuid4()
    communication.recipient = "customer@example.com"
    communication.subject = "Update"
    communication.body = "Hello"

    await service.send_communication(
        dispute_id=uuid4(),
        communication=communication,
        case=None,
        use_thread=False,
    )

    comm_repo.update_thread_metadata.assert_not_called()
    audit_service.log_event.assert_awaited_once()
    assert (
        audit_service.log_event.await_args.kwargs["action"] == "OUTBOUND_EMAIL_FAILED"
    )


@pytest.mark.asyncio
async def test_ar_service_client_send_email():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "gmail_message_id": "msg-1",
        "gmail_thread_id": "thread-1",
        "rfc_message_id": "<msg-1@gmail.com>",
        "sent": True,
    }

    mock_post = AsyncMock(return_value=mock_response)
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.post = mock_post
    mock_client.__aexit__.return_value = None

    import httpx

    original_async_client = httpx.AsyncClient

    class _ClientFactory:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return mock_client.__aenter__.return_value

        async def __aexit__(self, *args):
            return None

    httpx.AsyncClient = _ClientFactory  # type: ignore[misc, assignment]
    try:
        client = ARServiceClient(token="test-token")
        result = await client.send_email(
            {
                "to": "customer@example.com",
                "subject": "Hello",
                "body": "Body",
            }
        )
    finally:
        httpx.AsyncClient = original_async_client  # type: ignore[misc]

    assert result["sent"] is True
    assert result["gmail_message_id"] == "msg-1"
