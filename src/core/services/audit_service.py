import logging
from uuid import UUID

from src.data.repositories.other_repositories import ActivityRepository
from src.observability.logging.logger import logger


class AuditService:
    def __init__(self, activity_repo: ActivityRepository):
        self.activity_repo = activity_repo

    async def log_event(
        self,
        *,
        dispute_id: UUID,
        action: str,
        performed_by: UUID | None = None,
        metadata: dict | None = None,
    ) -> None:
        """Logs an audit event in the database activity trail and the structured logger."""
        logger.info(
            "Audit Event [%s]: dispute=%s, performed_by=%s, metadata=%s",
            action,
            dispute_id,
            performed_by,
            metadata,
        )
        await self.activity_repo.create_activity(
            dispute_id=dispute_id,
            activity_type=action,
            metadata=metadata,
            performed_by=performed_by,
        )
