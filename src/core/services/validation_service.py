from uuid import UUID

from src.core.exceptions.business_exceptions import (
    ARServiceClientException,
    ValidationException,
)
from src.data.clients.ar_service_client import ARServiceClient


class ValidationService:
    def __init__(self, ar_client: ARServiceClient):
        self.ar_client = ar_client

    async def lookup_invoice_by_number(
        self, invoice_number: str, custom_token: str | None = None
    ) -> dict | None:
        """Returns AR invoice metadata for a number, or None when not found."""
        return await self.ar_client.lookup_invoice_by_number(
            invoice_number, custom_token
        )

    async def validate_invoice(
        self, invoice_id: UUID, custom_token: str | None = None
    ) -> None:
        """Validates that an invoice exists in the AR system and is not cancelled."""
        try:
            invoice = await self.ar_client.get_invoice(invoice_id, custom_token)
        except ARServiceClientException as e:
            if e.status_code == 404:
                raise ValidationException(
                    f"Invoice validation failed: Invoice {invoice_id} does not exist."
                )
            raise ValidationException(
                f"Invoice validation failed due to communication error: {str(e)}"
            )

        status = invoice.get("status")
        if status == "CANCELLED":
            raise ValidationException(
                f"Invoice validation failed: Invoice {invoice_id} is CANCELLED."
            )
