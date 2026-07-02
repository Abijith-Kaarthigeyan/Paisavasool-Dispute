from datetime import UTC, datetime
from uuid import UUID

from src.core.config.settings import Settings
from src.core.exceptions.business_exceptions import ValidationException
from src.core.services.audit_service import AuditService
from src.data.models.postgres.sla import DisputeSLA
from src.data.repositories.dispute_repository import DisputeRepository
from src.data.repositories.sla_repository import SLARepository
from src.observability.logging.logger import logger

TERMINAL_DISPUTE_STATUSES = frozenset({"CLOSED", "RESOLVED", "FAILED"})
WAITING_CUSTOMER_STATUS = "WAITING_CUSTOMER"
PAUSE_REASON_WAITING_CUSTOMER = "WAITING_CUSTOMER"
PAUSE_REASON_AWAITING_CUSTOMER_REPLY = "AWAITING_CUSTOMER_REPLY"
PAUSE_SLA_TILL_REPLY_STATUSES = frozenset(
    {
        "WAITING_ASSOCIATE_APPROVAL",
        "WAITING_INTERNAL_TEAM",
        "WAITING_PAYMENT_REVIEW",
    }
)


class SLAService:
    def __init__(
        self,
        sla_repo: SLARepository,
        dispute_repo: DisputeRepository,
        audit_service: AuditService,
        settings: Settings,
    ):
        self.sla_repo = sla_repo
        self.dispute_repo = dispute_repo
        self.audit_service = audit_service
        self.settings = settings

    def _get_sla_minutes_for_category(self, category: str) -> int:
        """Determines the SLA duration in minutes based on category from application settings."""
        if category in ["PAYMENT_ALREADY_DONE", "PAYMENT_NOT_REFLECTED"]:
            hours = self.settings.DISPUTE_SLA_PAYMENT_HOURS
        elif category in ["AMENDMENT", "DUPLICATE_INVOICE"]:
            hours = self.settings.DISPUTE_SLA_AMENDMENT_HOURS
        elif category in ["QUALITY", "LATE_DELIVERY", "OTHER"]:
            hours = self.settings.DISPUTE_SLA_OPERATIONAL_HOURS
        else:
            hours = self.settings.DISPUTE_SLA_OPERATIONAL_HOURS

        return hours * 60

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value

    async def _pause_sla(
        self,
        sla: DisputeSLA,
        dispute_id: UUID,
        now: datetime,
        pause_reason: str,
        *,
        communication_id: UUID | None = None,
    ) -> None:
        if sla.is_paused:
            return

        sla.is_paused = True
        sla.paused_at = now
        sla.pause_reason = pause_reason
        await self.sla_repo.update_sla(sla)

        metadata: dict[str, str] = {
            "paused_at": now.isoformat(),
            "reason": pause_reason,
        }
        if communication_id is not None:
            metadata["communication_id"] = str(communication_id)

        await self.audit_service.log_event(
            dispute_id=dispute_id,
            action="SLA_PAUSED",
            metadata=metadata,
        )

    async def _resume_sla(
        self, sla: DisputeSLA, dispute_id: UUID, now: datetime
    ) -> None:
        if not sla.is_paused:
            return

        paused_at = self._as_utc(sla.paused_at or sla.started_at)
        paused_duration_minutes = (now - paused_at).total_seconds() / 60.0
        sla.accumulated_paused_minutes += paused_duration_minutes
        sla.is_paused = False
        sla.paused_at = None
        sla.pause_reason = None
        sla.resumed_at = now

        await self.sla_repo.update_sla(sla)

        await self.audit_service.log_event(
            dispute_id=dispute_id,
            action="SLA_RESUMED",
            metadata={
                "resumed_at": now.isoformat(),
                "paused_duration_minutes": paused_duration_minutes,
                "accumulated_paused_minutes": sla.accumulated_paused_minutes,
            },
        )

    async def _close_sla(
        self, sla: DisputeSLA, dispute_id: UUID, now: datetime, reason: str
    ) -> None:
        sla.is_paused = False
        sla.paused_at = None
        sla.pause_reason = None
        sla.status = "CLOSED"
        await self.sla_repo.update_sla(sla)

        await self.audit_service.log_event(
            dispute_id=dispute_id,
            action="SLA_CLOSED",
            metadata={"closed_at": now.isoformat(), "reason": reason},
        )

    async def create_sla(self, dispute_id: UUID) -> DisputeSLA:
        """Calculates and persists a new SLA for a dispute."""
        dispute = await self.dispute_repo.get_by_id(dispute_id)
        if not dispute:
            raise ValidationException("Dispute not found.")

        existing_sla = await self.sla_repo.get_by_dispute_id(dispute_id)
        if existing_sla:
            return existing_sla

        sla_minutes = self._get_sla_minutes_for_category(dispute.dispute_category)
        now = datetime.now(UTC)

        sla = await self.sla_repo.create_sla(
            dispute_id=dispute_id,
            sla_minutes=sla_minutes,
            started_at=now,
            is_paused=False,
            current_percentage=0.0,
            status="ON_TRACK",
        )

        await self.audit_service.log_event(
            dispute_id=dispute_id,
            action="SLA_CREATED",
            metadata={"sla_minutes": sla_minutes},
        )

        return sla

    async def handle_status_change(
        self, dispute_id: UUID, old_status: str, new_status: str
    ) -> None:
        """Pause SLA only for WAITING_CUSTOMER; close SLA on terminal dispute statuses."""
        sla = await self.sla_repo.get_by_dispute_id(dispute_id)
        if not sla:
            return

        now = datetime.now(UTC)

        if new_status in TERMINAL_DISPUTE_STATUSES:
            if sla.is_paused:
                await self._resume_sla(sla, dispute_id, now)
            await self._close_sla(sla, dispute_id, now, f"DISPUTE_{new_status}")
            return

        if new_status == WAITING_CUSTOMER_STATUS:
            await self._pause_sla(sla, dispute_id, now, PAUSE_REASON_WAITING_CUSTOMER)
            return

        if sla.is_paused and sla.pause_reason != PAUSE_REASON_AWAITING_CUSTOMER_REPLY:
            await self._resume_sla(sla, dispute_id, now)

    async def _sync_pause_with_dispute_status(
        self, sla: DisputeSLA, dispute_id: UUID, dispute_status: str, now: datetime
    ) -> None:
        """Keep SLA pause state aligned with dispute status when transitions were missed."""
        if dispute_status in TERMINAL_DISPUTE_STATUSES:
            if sla.is_paused:
                await self._resume_sla(sla, dispute_id, now)
            return

        if dispute_status == WAITING_CUSTOMER_STATUS:
            await self._pause_sla(sla, dispute_id, now, PAUSE_REASON_WAITING_CUSTOMER)
            return

        if sla.is_paused and sla.pause_reason == PAUSE_REASON_AWAITING_CUSTOMER_REPLY:
            return

        if sla.is_paused:
            await self._resume_sla(sla, dispute_id, now)

    async def pause_for_customer_reply(
        self, dispute_id: UUID, communication_id: UUID
    ) -> None:
        """Pause SLA until a correlated customer inbound reply is received."""
        dispute = await self.dispute_repo.get_by_id(dispute_id)
        if not dispute:
            raise ValidationException("Dispute not found.")

        if dispute.status not in PAUSE_SLA_TILL_REPLY_STATUSES:
            raise ValidationException(
                "SLA can only be paused until customer reply in waiting states."
            )

        sla = await self.sla_repo.get_by_dispute_id(dispute_id)
        if not sla:
            return

        now = datetime.now(UTC)
        await self._pause_sla(
            sla,
            dispute_id,
            now,
            PAUSE_REASON_AWAITING_CUSTOMER_REPLY,
            communication_id=communication_id,
        )

    async def resume_on_customer_reply(self, dispute_id: UUID) -> None:
        """Resume SLA only when paused by associate-initiated customer-reply wait."""
        sla = await self.sla_repo.get_by_dispute_id(dispute_id)
        if not sla or not sla.is_paused:
            return
        if sla.pause_reason != PAUSE_REASON_AWAITING_CUSTOMER_REPLY:
            return

        now = datetime.now(UTC)
        await self._resume_sla(sla, dispute_id, now)

    async def calculate_progress(self, dispute_id: UUID) -> DisputeSLA | None:
        """Calculates active time elapsed, percentage of SLA used, and updates breach status."""
        sla = await self.sla_repo.get_by_dispute_id(dispute_id)
        if not sla:
            logger.warning(
                "SLA not found for dispute %s. Skipping progress calculation.",
                dispute_id,
            )
            return None

        dispute = await self.dispute_repo.get_by_id(dispute_id)
        if not dispute:
            raise ValidationException("Dispute not found.")

        now = datetime.now(UTC)
        await self._sync_pause_with_dispute_status(sla, dispute_id, dispute.status, now)
        sla = await self.sla_repo.get_by_dispute_id(dispute_id)
        if not sla:
            return None

        started_at = self._as_utc(sla.started_at)

        if dispute.status in TERMINAL_DISPUTE_STATUSES:
            end_at = (
                dispute.closed_at or dispute.resolved_at or dispute.updated_at or now
            )
            end_at = self._as_utc(end_at)
            elapsed_total_minutes = (end_at - started_at).total_seconds() / 60.0
            sla.is_paused = False
            sla.paused_at = None
            sla.pause_reason = None
            sla.status = "CLOSED"
        elif sla.is_paused:
            paused_at = self._as_utc(sla.paused_at or now)
            elapsed_total_minutes = (paused_at - started_at).total_seconds() / 60.0
        else:
            elapsed_total_minutes = (now - started_at).total_seconds() / 60.0

        active_elapsed_minutes = elapsed_total_minutes - sla.accumulated_paused_minutes
        if active_elapsed_minutes < 0:
            active_elapsed_minutes = 0.0

        percentage = (active_elapsed_minutes / sla.sla_minutes) * 100.0
        sla.current_percentage = round(percentage, 2)

        old_status = sla.status

        if dispute.status in TERMINAL_DISPUTE_STATUSES:
            sla.status = "CLOSED"
        elif percentage >= 100.0:
            sla.status = "BREACHED"
            if not sla.breached_at:
                breach_at = sla.paused_at if sla.is_paused else now
                sla.breached_at = self._as_utc(breach_at)
        elif percentage >= 80.0:
            sla.status = "AT_RISK"
        else:
            sla.status = "ON_TRACK"

        await self.sla_repo.update_sla(sla)

        if old_status != sla.status:
            await self.audit_service.log_event(
                dispute_id=dispute_id,
                action="STATUS_CHANGED",
                metadata={
                    "field": "sla_status",
                    "old_value": old_status,
                    "new_value": sla.status,
                },
            )

        return sla
