"""Evidence snapshot service for capturing reproducibility data of disputes."""

from typing import Any
from uuid import UUID

from src.data.repositories.other_repositories import EvidenceSnapshotRepository


class EvidenceSnapshotService:
    """Captures and stores email, invoice, and validation snapshots in DB."""

    def __init__(self, snapshot_repo: EvidenceSnapshotRepository) -> None:
        self.snapshot_repo = snapshot_repo

    async def capture_snapshots(
        self,
        *,
        dispute_id: UUID,
        email_snapshot: dict[str, Any],
        invoice_snapshot: dict[str, Any],
        validation_snapshot: dict[str, Any],
    ) -> None:
        """Saves customer email, invoice details, and validation logs to snapshots."""
        await self.snapshot_repo.create_evidence_snapshot(
            dispute_id=dispute_id,
            snapshot_type="customer_email",
            snapshot_data=email_snapshot,
        )
        await self.snapshot_repo.create_evidence_snapshot(
            dispute_id=dispute_id,
            snapshot_type="invoice_snapshot",
            snapshot_data=invoice_snapshot,
        )
        await self.snapshot_repo.create_evidence_snapshot(
            dispute_id=dispute_id,
            snapshot_type="validation_snapshot",
            snapshot_data=validation_snapshot,
        )
