"""Workflow tests for outbound email dispatch."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.core.workflow.graph import (
    _dispatch_outbound_email,
)


@pytest.mark.asyncio
async def test_persist_outbound_customer_mail_dispatches_email():
    db = AsyncMock()
    dispute_id = uuid4()
    dispute = AsyncMock()
    dispute.id = dispute_id
    dispute.case = None

    agent_res = {
        "recipient": "customer@example.com",
        "subject": "Dispute update",
        "body": "We are reviewing your case.",
    }

    mock_comm = AsyncMock()
    mock_comm.id = uuid4()

    with (
        patch("src.core.workflow.graph.CommunicationRepository") as comm_repo_cls,
        patch(
            "src.core.workflow.graph._dispatch_outbound_email",
            new_callable=AsyncMock,
        ) as mock_dispatch,
        patch(
            "src.core.workflow.graph.DisputeMailAgent.generate_mail",
            new_callable=AsyncMock,
            return_value=agent_res,
        ),
        patch("src.core.workflow.graph.ActivityRepository") as activity_repo_cls,
        patch("src.core.workflow.graph.CommentRepository") as comment_repo_cls,
        patch("src.core.workflow.graph._conversation_history_service") as conv_svc,
    ):
        activity_repo_cls.return_value.list_activities_for_dispute = AsyncMock(
            return_value=[]
        )
        comment_repo_cls.return_value.list_comments_for_dispute = AsyncMock(
            return_value=[]
        )
        comm_repo_cls.return_value.create_communication = AsyncMock(
            return_value=mock_comm
        )
        conv_svc.return_value.build_customer_conversation_text = AsyncMock(
            return_value="Customer message"
        )

        from src.core.workflow.graph import _send_customer_outbound_mail

        await _send_customer_outbound_mail(
            db=db,
            dispute_id=dispute_id,
            dispute=dispute,
            state={"customer_email": "customer@example.com", "metadata": {}},
            outcome="NEED_MORE_INFO",
        )

    mock_dispatch.assert_awaited_once_with(db, dispute, mock_comm, use_thread=True)


@pytest.mark.asyncio
async def test_dispatch_outbound_email_delegates_to_service():
    db = AsyncMock()
    dispute = AsyncMock()
    dispute.id = uuid4()
    dispute.case = None
    comm = AsyncMock()

    with patch("src.core.workflow.graph._outbound_email_service") as service_factory:
        service = AsyncMock()
        service_factory.return_value = service

        await _dispatch_outbound_email(db, dispute, comm, use_thread=False)

    service.send_communication.assert_awaited_once_with(
        dispute_id=dispute.id,
        communication=comm,
        case=None,
        use_thread=False,
    )
