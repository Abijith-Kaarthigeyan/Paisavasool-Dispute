from datetime import UTC, datetime
from uuid import UUID

from src.core.config.settings import Settings
from src.core.exceptions.business_exceptions import ValidationException
from src.core.services.audit_service import AuditService
from src.data.models.postgres.sla import DisputeSLA
from src.data.repositories.dispute_repository import DisputeRepository
from src.data.repositories.sla_repository import SLARepository


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
            # Default fallback to operational
            hours = self.settings.DISPUTE_SLA_OPERATIONAL_HOURS

        return hours * 60

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
        """Triggers SLA Pause / Resume based on status transition rules."""
        sla = await self.sla_repo.get_by_dispute_id(dispute_id)
        if not sla:
            return

        now = datetime.now(UTC)

        # Transitioning TO WAITING_CUSTOMER: Pause SLA
        if new_status == "WAITING_CUSTOMER" and not sla.is_paused:
            sla.is_paused = True
            sla.paused_at = now
            await self.sla_repo.update_sla(sla)

            await self.audit_service.log_event(
                dispute_id=dispute_id,
                action="SLA_PAUSED",
                metadata={"paused_at": now.isoformat()},
            )

        # Transitioning AWAY from WAITING_CUSTOMER: Resume SLA
        elif (
            old_status == "WAITING_CUSTOMER"
            and new_status != "WAITING_CUSTOMER"
            and sla.is_paused
        ):
            paused_at = (sla.paused_at or sla.started_at).replace(tzinfo=UTC)
            paused_duration_minutes = (now - paused_at).total_seconds() / 60.0
            sla.accumulated_paused_minutes += paused_duration_minutes
            sla.is_paused = False
            sla.paused_at = None
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

    async def calculate_progress(self, dispute_id: UUID) -> DisputeSLA:
        """Calculates active time elapsed, percentage of SLA used, and updates breach status."""
        sla = await self.sla_repo.get_by_dispute_id(dispute_id)
        if not sla:
            raise ValidationException("SLA not found for dispute.")

        now = datetime.now(UTC)
        started_at = sla.started_at.replace(tzinfo=UTC)

        if sla.is_paused:
            paused_at = (sla.paused_at or now).replace(tzinfo=UTC)
            elapsed_total_minutes = (paused_at - started_at).total_seconds() / 60.0
        else:
            elapsed_total_minutes = (now - started_at).total_seconds() / 60.0

        active_elapsed_minutes = elapsed_total_minutes - sla.accumulated_paused_minutes
        if active_elapsed_minutes < 0:
            active_elapsed_minutes = 0.0

        percentage = (active_elapsed_minutes / sla.sla_minutes) * 100.0
        sla.current_percentage = round(percentage, 2)

        old_status = sla.status

        if percentage >= 100.0:
            sla.status = "BREACHED"
            if not sla.breached_at:
                sla.breached_at = now
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
