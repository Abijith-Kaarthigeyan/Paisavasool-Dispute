from typing import Any
from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_process_init

from src.core.config.settings import settings

celery_app = Celery(
    "dispute_service",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)


@worker_process_init.connect  # type: ignore[untyped-decorator]
def init_worker_process(*args: Any, **kwargs: Any) -> None:
    """Disposes database engine pool upon worker child process start to prevent shared loop sockets."""
    try:
        from src.data.clients.postgres_client import engine

        engine.sync_engine.dispose()
    except Exception as e:
        import logging

        logging.getLogger("paisavasool.dispute").error(
            "Failed to dispose engine connection pool on worker startup: %s", e
        )


celery_app.conf.update(
    task_default_queue="dispute_processing",
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    broker_connection_retry_on_startup=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Auto-discover tasks in src.infrastructure.celery.tasks
    imports=["src.infrastructure.celery.tasks"],
    beat_schedule={
        "run-sla-monitoring-job": {
            "task": "src.infrastructure.celery.tasks.run_sla_monitoring",
            "schedule": crontab(minute="*/15"),
        },
    },
)

