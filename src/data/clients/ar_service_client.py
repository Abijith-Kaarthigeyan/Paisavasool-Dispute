from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
from jose import jwt

from src.core.config.settings import settings
from src.core.exceptions.business_exceptions import ARServiceClientException


class ARServiceClient:
    def __init__(self, token: str | None = None):
        self.base_url = settings.AR_SERVICE_URL
        self.token = token

    def _get_headers(self, custom_token: str | None = None) -> dict[str, str]:
        """Prepares headers, prioritizing passed custom token, then instance token, then system token."""
        tok = custom_token or self.token
        if not tok:
            # Generate a system JWT token signed with shared SECRET_KEY
            payload = {
                "sub": "dispute-service",
                "email": "system@dispute-service.internal",
                "role": "ADMIN",
                "is_active": True,
                "service": True,
                "exp": datetime.now(UTC) + timedelta(hours=1),
            }
            tok = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

        return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}

    async def get_invoice(
        self, invoice_id: UUID, custom_token: str | None = None
    ) -> dict:
        """Fetch invoice basic metadata from AR service."""
        url = f"{self.base_url}/api/v1/invoices/{invoice_id}"
        headers = self._get_headers(custom_token)
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, headers=headers, timeout=5.0)
                if resp.status_code == 404:
                    raise ARServiceClientException(
                        f"Invoice {invoice_id} not found in AR.", status_code=404
                    )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            raise ARServiceClientException(
                f"AR Service HTTP error: {e.response.text}",
                status_code=e.response.status_code,
            )
        except Exception as e:
            raise ARServiceClientException(f"AR Service communication error: {str(e)}")

    async def get_invoice_details(
        self, invoice_id: UUID, custom_token: str | None = None
    ) -> dict:
        """Fetch full invoice details, including line items."""
        invoice = await self.get_invoice(invoice_id, custom_token)
        # Fetch items
        url = f"{self.base_url}/api/v1/invoices/{invoice_id}/items"
        headers = self._get_headers(custom_token)
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, headers=headers, timeout=5.0)
                resp.raise_for_status()
                invoice["items"] = resp.json()
                return invoice
        except Exception:
            # Fall back to invoice metadata if items fail
            invoice["items"] = []
            return invoice

    async def get_customer(
        self, customer_id: UUID, custom_token: str | None = None
    ) -> dict:
        """Fetch customer profile and transactions from AR service."""
        url = f"{self.base_url}/api/v1/customers/{customer_id}"
        headers = self._get_headers(custom_token)
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, headers=headers, timeout=5.0)
                if resp.status_code == 404:
                    raise ARServiceClientException(
                        f"Customer {customer_id} not found in AR.", status_code=404
                    )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            raise ARServiceClientException(
                f"AR Service HTTP error: {e.response.text}",
                status_code=e.response.status_code,
            )
        except Exception as e:
            raise ARServiceClientException(f"AR Service communication error: {str(e)}")

    async def get_payment_status(
        self, invoice_id: UUID, custom_token: str | None = None
    ) -> str:
        """Fetch payment status of an invoice. Returns 'PAID' or invoice status."""
        try:
            invoice = await self.get_invoice(invoice_id, custom_token)
            return invoice.get("status", "UNPAID")
        except Exception:
            return "UNPAID"

    async def get_collection_case(
        self, case_id: UUID, custom_token: str | None = None
    ) -> dict:
        """Fetch a collection case by its ID."""
        url = f"{self.base_url}/api/v1/collections/{case_id}"
        headers = self._get_headers(custom_token)
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, headers=headers, timeout=5.0)
                if resp.status_code == 404:
                    raise ARServiceClientException(
                        f"Collection case {case_id} not found in AR.", status_code=404
                    )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            raise ARServiceClientException(
                f"AR Service HTTP error: {e.response.text}",
                status_code=e.response.status_code,
            )
        except Exception as e:
            raise ARServiceClientException(f"AR Service communication error: {str(e)}")

    async def pause_collections(
        self, invoice_id: UUID, custom_token: str | None = None
    ) -> dict:
        """Pause collections activity for an invoice by calling AR Service."""
        url = f"{self.base_url}/api/v1/collections/invoices/{invoice_id}/dispute"
        headers = self._get_headers(custom_token)
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, headers=headers, timeout=5.0)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            raise ARServiceClientException(
                f"AR Service HTTP error: {e.response.text}",
                status_code=e.response.status_code,
            )
        except Exception as e:
            raise ARServiceClientException(f"AR Service communication error: {str(e)}")

    async def resume_collections(
        self, invoice_id: UUID, custom_token: str | None = None
    ) -> dict:
        """Resume collections activity for an invoice by calling AR Service."""
        url = f"{self.base_url}/api/v1/collections/invoices/{invoice_id}/resolve"
        headers = self._get_headers(custom_token)
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, headers=headers, timeout=5.0)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            raise ARServiceClientException(
                f"AR Service HTTP error: {e.response.text}",
                status_code=e.response.status_code,
            )
        except Exception as e:
            raise ARServiceClientException(f"AR Service communication error: {str(e)}")

    async def amend_invoice(
        self,
        invoice_id: UUID,
        payload: dict,
        custom_token: str | None = None,
    ) -> dict:
        """Apply an invoice amendment in AR service."""
        url = f"{self.base_url}/api/v1/invoices/{invoice_id}/amend"
        headers = self._get_headers(custom_token)
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    url, headers=headers, json=payload, timeout=30.0
                )
                if resp.status_code == 404:
                    raise ARServiceClientException(
                        f"Invoice {invoice_id} not found in AR.", status_code=404
                    )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            raise ARServiceClientException(
                f"AR Service HTTP error: {e.response.text}",
                status_code=e.response.status_code,
            )
        except ARServiceClientException:
            raise
        except Exception as e:
            raise ARServiceClientException(f"AR Service communication error: {str(e)}")

    async def get_invoice_versions(
        self, invoice_id: UUID, custom_token: str | None = None
    ) -> dict:
        """List version history for an invoice."""
        url = f"{self.base_url}/api/v1/invoices/{invoice_id}/versions"
        headers = self._get_headers(custom_token)
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, headers=headers, timeout=5.0)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            raise ARServiceClientException(
                f"AR Service HTTP error: {e.response.text}",
                status_code=e.response.status_code,
            )
        except Exception as e:
            raise ARServiceClientException(f"AR Service communication error: {str(e)}")

    async def find_payment_reference(
        self, reference_number: str, custom_token: str | None = None
    ) -> str:
        """Searches payments for a reference number and returns status: SETTLED, REJECTED, or REVIEW_QUEUE."""
        url = f"{self.base_url}/api/v1/payments/search/reference?reference={reference_number}"
        headers = self._get_headers(custom_token)
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, headers=headers, timeout=5.0)
                if resp.status_code == 404:
                    return "REJECTED"
                resp.raise_for_status()
                data = resp.json()
                status = data.get("status", "").upper()
                if status == "SETTLED":
                    return "SETTLED"
                elif status == "REJECTED":
                    return "REJECTED"
                elif status in ["REVIEW_REQUIRED", "PROCESSING", "PENDING"]:
                    return "REVIEW_QUEUE"
                return "REVIEW_QUEUE"
        except Exception:
            return "REVIEW_QUEUE"
