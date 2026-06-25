import asyncio
import traceback
from collections.abc import Coroutine
from typing import Any
from uuid import UUID

from celery import Task

from src.infrastructure.celery.celery_app import celery_app
from src.observability.logging.logger import logger


async def _wrap_coro(coro: Coroutine[Any, Any, Any]) -> Any:
    try:
        return await coro
    finally:
        try:
            from src.data.clients.postgres_client import engine

            await engine.dispose()
        except Exception as e:
            logger.error("Failed to dispose database engine in task wrapper: %s", e)


def _run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    """Runs an async coroutine synchronously, supporting running event loops safely."""
    wrapped = _wrap_coro(coro)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(asyncio.run, wrapped)
            return future.result()
    else:
        return asyncio.run(wrapped)


async def run_sla_monitoring_async() -> int:
    """Recalculates SLA progress and checks escalations for all open/active disputes."""
    from src.core.config.settings import settings
    from src.core.services.audit_service import AuditService
    from src.core.services.escalation_service import EscalationService
    from src.core.services.sla_service import SLAService
    from src.data.clients.postgres_client import AsyncSessionLocal
    from src.data.repositories.dispute_repository import DisputeRepository
    from src.data.repositories.escalation_repository import EscalationRepository
    from src.data.repositories.other_repositories import ActivityRepository
    from src.data.repositories.sla_repository import SLARepository

    async with AsyncSessionLocal() as db:
        dispute_repo = DisputeRepository(db)
        sla_repo = SLARepository(db)
        escalation_repo = EscalationRepository(db)
        activity_repo = ActivityRepository(db)

        audit_service = AuditService(activity_repo)
        sla_service = SLAService(sla_repo, dispute_repo, audit_service, settings)
        escalation_service = EscalationService(
            escalation_repo, sla_repo, dispute_repo, audit_service
        )

        # Get open/active disputes
        active_statuses = [
            "OPEN",
            "IN_REVIEW",
            "WAITING_CUSTOMER",
            "WAITING_INTERNAL_TEAM",
            "WAITING_ASSOCIATE_APPROVAL",
            "ESCALATED",
        ]
        disputes = await dispute_repo.list_disputes(limit=1000)
        active_disputes = [d for d in disputes if d.status in active_statuses]

        count = 0
        for dispute in active_disputes:
            try:
                # 1. Recalculate SLA
                await sla_service.calculate_progress(dispute.id)
                # 2. Check and trigger escalations
                await escalation_service.check_and_trigger_escalations(dispute.id)
                count += 1
            except Exception as e:
                logger.error(
                    "Failed to run SLA/Escalation check for dispute %s: %s",
                    dispute.id,
                    str(e),
                )

        await db.commit()
        return count


async def handle_task_failure_async(
    dispute_id_str: str, err: Exception, stack: str, retries: int
) -> None:
    """Records a task workflow execution failure to the Review Queue on max retries exhaustion."""
    from src.core.services.audit_service import AuditService
    from src.data.clients.postgres_client import AsyncSessionLocal
    from src.data.repositories.dispute_repository import DisputeRepository
    from src.data.repositories.other_repositories import ActivityRepository
    from src.data.repositories.review_queue_repository import ReviewQueueRepository

    dispute_id = UUID(dispute_id_str)

    async with AsyncSessionLocal() as db:
        dispute_repo = DisputeRepository(db)
        review_repo = ReviewQueueRepository(db)
        activity_repo = ActivityRepository(db)
        audit_service = AuditService(activity_repo)

        dispute = await dispute_repo.get_by_id(dispute_id)
        if not dispute:
            return

        # 1. Update dispute status to FAILED
        old_status = dispute.status
        dispute.status = "FAILED"
        await dispute_repo.update_dispute(dispute)

        from src.core.config.settings import settings
        from src.core.services.sla_service import SLAService
        from src.data.repositories.sla_repository import SLARepository

        sla_service = SLAService(
            SLARepository(db), dispute_repo, audit_service, settings
        )
        await sla_service.handle_status_change(dispute_id, old_status, "FAILED")

        # 2. Add item to dispute_review_queue
        await review_repo.create_review_queue_item(
            dispute_id=dispute_id,
            review_reason="WORKFLOW_MAX_RETRIES_EXCEEDED",
            status="FAILED",
            error_message=str(err),
            stack_trace=stack,
            retry_count=retries,
        )

        # 3. Log activity trail
        await audit_service.log_event(
            dispute_id=dispute_id,
            action="WORKFLOW_FAILED",
            metadata={
                "error": str(err),
                "retry_count": retries,
            },
        )

        await db.commit()


@celery_app.task(
    bind=True,
    name="src.infrastructure.celery.tasks.run_sla_monitoring",
    max_retries=2,
    queue="dispute_processing",
)
def run_sla_monitoring(self: Task) -> int:
    """Celery task to run periodic SLA progress monitoring on all active disputes."""
    try:
        return _run_async(run_sla_monitoring_async())
    except Exception as e:
        retry_count = self.request.retries
        if retry_count < self.max_retries:
            raise self.retry(exc=e, countdown=10)
        raise e


async def process_dispute_case_async(case_id_str: str) -> None:
    """Runs Case Intake and TriageAgent nodes, then spawns workflows for each generated dispute."""
    from src.core.workflow.graph import get_graph
    from src.data.clients.postgres_client import AsyncSessionLocal
    from src.data.repositories.case_repository import CaseRepository

    case_id = UUID(case_id_str)
    async with AsyncSessionLocal() as db:
        case_repo = CaseRepository(db)
        case = await case_repo.get_by_id(case_id)
        if not case:
            return

        config = {
            "configurable": {
                "db": db,
                "thread_id": str(case_id),
            }
        }
        graph = get_graph()
        initial_state = {
            "customer_email": case.customer_email,
            "email_subject": case.email_subject or "",
            "email_body": case.email_body or "",
            "raw_content": case.raw_content or "",
            "message_id": case.original_message_id,
            "workflow_status": "START",
            "current_node": "START",
            "requires_human_review": False,
            "errors": [],
            "metadata": {},
        }
        final_state = await graph.ainvoke(initial_state, config)
        await db.commit()

        metadata = final_state.get("metadata") if isinstance(final_state, dict) else {}
        resume_dispute_id = metadata.get("resume_dispute_id")
        if resume_dispute_id:
            from src.infrastructure.celery.tasks import (
                resume_dispute_workflow_after_customer_reply,
            )

            resume_dispute_workflow_after_customer_reply.delay(
                resume_dispute_id,
                case.email_subject or "",
                case.email_body or "",
                case.raw_content or "",
            )


@celery_app.task(
    bind=True,
    name="src.infrastructure.celery.tasks.process_dispute_case",
    max_retries=2,
    queue="dispute_processing",
)
def process_dispute_case(self: Task, case_id_str: str) -> str:
    """Celery task running the case intake and triage graph steps, producing disputes."""
    try:
        _run_async(process_dispute_case_async(case_id_str))
        return "SUCCESS"
    except Exception as e:
        retry_count = self.request.retries
        if retry_count < self.max_retries:
            raise self.retry(exc=e, countdown=10)
        raise e


async def process_dispute_workflow_async(dispute_id_str: str) -> None:
    """Runs dispute validation, correlation, assignment, and routing nodes asynchronously."""
    from langgraph.errors import NodeInterrupt

    from src.core.workflow.graph import get_graph
    from src.data.clients.postgres_client import AsyncSessionLocal
    from src.data.repositories.case_repository import CaseRepository
    from src.data.repositories.dispute_repository import DisputeRepository

    dispute_id = UUID(dispute_id_str)
    async with AsyncSessionLocal() as db:
        dispute_repo = DisputeRepository(db)
        dispute = await dispute_repo.get_by_id(dispute_id)
        if not dispute:
            return

        case_repo = CaseRepository(db)
        case = await case_repo.get_by_id(dispute.case_id)
        if not case:
            return

        config = {
            "configurable": {
                "db": db,
                "thread_id": str(dispute_id),
            }
        }
        graph = get_graph()
        initial_state = {
            "case_id": dispute.case_id,
            "dispute_id": dispute.id,
            "dispute_number": dispute.dispute_number,
            "invoice_id": dispute.invoice_id,
            "invoice_number": dispute.invoice_number,
            "customer_id": dispute.customer_id,
            "customer_email": case.customer_email,
            "email_subject": case.email_subject or "",
            "email_body": case.email_body or "",
            "raw_content": case.raw_content or "",
            "message_id": case.original_message_id,
            "dispute_category": dispute.dispute_category,
            "assigned_to": dispute.assigned_to,
            "workflow_status": "START",
            "current_node": "correlation_node",
            "requires_human_review": False,
            "errors": [],
            "metadata": {},
        }
        try:
            final_state = await graph.ainvoke(initial_state, config)
            await db.commit()

            metadata = (
                final_state.get("metadata") if isinstance(final_state, dict) else {}
            )
            resume_dispute_id = metadata.get("resume_dispute_id")
            if resume_dispute_id:
                from src.infrastructure.celery.tasks import (
                    resume_dispute_workflow_after_customer_reply,
                )

                resume_dispute_workflow_after_customer_reply.delay(
                    resume_dispute_id,
                    case.email_subject or "",
                    case.email_body or "",
                    case.raw_content or "",
                )
        except NodeInterrupt as e:
            await db.commit()
            logger.info("Workflow paused on human interrupt: %s", str(e))
        except Exception as e:
            await db.rollback()
            raise e


@celery_app.task(
    bind=True,
    name="src.infrastructure.celery.tasks.process_dispute_workflow",
    max_retries=3,
    queue="dispute_processing",
)
def process_dispute_workflow(self: Task, dispute_id_str: str) -> str:
    """Celery task executing LangGraph workflow, protected by Redis distributed locking."""
    import redis

    from src.core.config.settings import settings

    lock_key = f"dispute:{dispute_id_str}"
    redis_client = redis.Redis.from_url(settings.REDIS_URL)

    # Acquire Redis Distributed Lock (TTL 30 minutes)
    acquired = redis_client.set(lock_key, "locked", ex=1800, nx=True)
    if not acquired:
        logger.info(
            "Distributed lock for %s is already held. Requeuing task.", lock_key
        )
        raise self.retry(countdown=10)

    try:
        _run_async(process_dispute_workflow_async(dispute_id_str))
        return "SUCCESS"
    except Exception as e:
        retry_count = self.request.retries
        if retry_count < self.max_retries:
            raise self.retry(exc=e, countdown=5)
        else:
            stack = traceback.format_exc()
            _run_async(handle_task_failure_async(dispute_id_str, e, stack, retry_count))
            raise e
    finally:
        redis_client.delete(lock_key)


async def resume_dispute_workflow_after_customer_reply_async(
    dispute_id_str: str,
    email_subject: str,
    email_body: str,
    raw_content: str = "",
) -> None:
    """Resumes a paused dispute workflow after the customer replies with more information."""
    from langgraph.errors import NodeInterrupt

    from src.core.workflow.resume_service import DisputeResumeService
    from src.data.clients.postgres_client import AsyncSessionLocal
    from src.data.repositories.dispute_repository import DisputeRepository

    dispute_id = UUID(dispute_id_str)
    async with AsyncSessionLocal() as db:
        dispute_repo = DisputeRepository(db)
        dispute = await dispute_repo.get_by_id(dispute_id)
        if not dispute:
            return

        resume_service = DisputeResumeService()
        state_updates = {
            "email_subject": email_subject,
            "email_body": email_body,
            "raw_content": raw_content
            or f"Subject: {email_subject}\nBody: {email_body}",
        }
        try:
            await resume_service.resume_workflow(
                db, dispute_id, state_updates=state_updates
            )
            await db.commit()
        except NodeInterrupt as e:
            await db.commit()
            logger.info(
                "Workflow paused again after customer reply for dispute %s: %s",
                dispute_id,
                str(e),
            )
        except Exception as e:
            await db.rollback()
            raise e


@celery_app.task(
    bind=True,
    name="src.infrastructure.celery.tasks.resume_dispute_workflow_after_customer_reply",
    max_retries=2,
    queue="dispute_processing",
)
def resume_dispute_workflow_after_customer_reply(
    self: Task,
    dispute_id_str: str,
    email_subject: str,
    email_body: str,
    raw_content: str = "",
) -> str:
    """Celery task to resume a dispute after a correlated customer reply email."""
    try:
        _run_async(
            resume_dispute_workflow_after_customer_reply_async(
                dispute_id_str, email_subject, email_body, raw_content
            )
        )
        return "SUCCESS"
    except Exception as e:
        retry_count = self.request.retries
        if retry_count < self.max_retries:
            raise self.retry(exc=e, countdown=10)
        raise e


async def resume_dispute_workflow_async(dispute_id_str: str) -> None:
    """Asynchronously resumes workflow via resume service."""
    from langgraph.errors import NodeInterrupt

    from src.core.workflow.resume_service import DisputeResumeService
    from src.data.clients.postgres_client import AsyncSessionLocal

    dispute_id = UUID(dispute_id_str)
    async with AsyncSessionLocal() as db:
        resume_service = DisputeResumeService()
        try:
            await resume_service.resume_workflow(db, dispute_id)
            await db.commit()
        except NodeInterrupt as e:
            await db.commit()
            logger.info("Workflow paused on human interrupt: %s", str(e))
        except Exception as e:
            await db.rollback()
            raise e


@celery_app.task(
    bind=True,
    name="src.infrastructure.celery.tasks.resume_dispute_workflow",
    max_retries=2,
    queue="dispute_processing",
)
def resume_dispute_workflow(self: Task, dispute_id_str: str) -> str:
    """Celery task to resume a dispute workflow from its checkpoint."""
    try:
        _run_async(resume_dispute_workflow_async(dispute_id_str))
        return "SUCCESS"
    except Exception as e:
        retry_count = self.request.retries
        if retry_count < self.max_retries:
            raise self.retry(exc=e, countdown=10)
        raise e


async def replay_failed_workflow_async(review_item_id: UUID) -> None:
    """Deletes/resolves review queue item and restarts the workflow task."""
    from src.core.services.audit_service import AuditService
    from src.data.clients.postgres_client import AsyncSessionLocal
    from src.data.repositories.dispute_repository import DisputeRepository
    from src.data.repositories.other_repositories import ActivityRepository
    from src.data.repositories.review_queue_repository import ReviewQueueRepository

    async with AsyncSessionLocal() as db:
        review_repo = ReviewQueueRepository(db)
        dispute_repo = DisputeRepository(db)
        activity_repo = ActivityRepository(db)
        audit_service = AuditService(activity_repo)

        item = await review_repo.get_by_id(review_item_id)
        if not item:
            return

        dispute = await dispute_repo.get_by_id(item.dispute_id)
        if not dispute:
            return

        # Restore status to OPEN or original
        dispute.status = "OPEN"
        await dispute_repo.update_dispute(dispute)

        # Mark review item as resolved
        item.status = "RESOLVED"
        await review_repo.update_review_queue_item(item)

        await audit_service.log_event(
            dispute_id=dispute.id,
            action="STATUS_CHANGED",
            metadata={"old_status": "FAILED", "new_status": "OPEN", "action": "replay"},
        )

        await db.commit()

        # Trigger workflow task again
        process_dispute_workflow.delay(str(dispute.id))


def replay_failed_workflow_sync(review_item_id: UUID) -> None:
    """Replays a failed workflow (invokable by APIs or admins)."""
    _run_async(replay_failed_workflow_async(review_item_id))
