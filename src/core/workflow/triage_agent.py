"""Dispute Triage Agent with fallback LLM/OpenRouter and regex processing."""

import json
import re
import time
from typing import Any

import httpx

from src.core.config.settings import settings
from src.observability.logging.logger import logger


class DisputeTriageAgent:
    """Triage agent extracting invoice numbers and dispute categories from emails."""

    # Map granular types to AMENDMENT
    CATEGORY_MAPPING = {
        "PRICING": "AMENDMENT",
        "TAX": "AMENDMENT",
        "QUANTITY": "AMENDMENT",
        "WRONG_PRODUCT": "AMENDMENT",
        "WRONG_CUSTOMER": "AMENDMENT",
        "WRONG_TOTAL": "AMENDMENT",
        "WRONG_DATE": "AMENDMENT",
        "WRONG_INVOICE_DATE": "AMENDMENT",
        "WRONG_DUE_DATE": "AMENDMENT",
    }

    VALID_CATEGORIES = {
        "AMENDMENT",
        "PAYMENT_ALREADY_DONE",
        "PAYMENT_NOT_REFLECTED",
        "DUPLICATE_INVOICE",
        "QUALITY",
        "LATE_DELIVERY",
        "OTHER",
    }

    @classmethod
    def normalize_category(cls, category: str) -> str:
        """Normalizes a raw category string using standard mappings."""
        upper_cat = category.upper().strip()
        # First map granular types
        mapped = cls.CATEGORY_MAPPING.get(upper_cat, upper_cat)
        # Verify it is one of the valid categories
        if mapped in cls.VALID_CATEGORIES:
            return mapped
        return "OTHER"

    @classmethod
    def normalize_invoice_number(cls, invoice_number: str) -> str:
        """Standardizes invoice numbers into the format INV-XXXX."""
        if not invoice_number:
            return ""
        clean = invoice_number.strip()
        match = re.match(r"(?i)^(?:invoice|inv)[^a-zA-Z0-9]*(.*)$", clean)
        if match:
            suffix = match.group(1).strip()
            suffix_clean = re.sub(r"[^a-zA-Z0-9]", "", suffix)
            return f"INV-{suffix_clean.upper()}"
        else:
            # If no "INV" prefix but is numeric, prefix with "INV-"
            clean_num = re.sub(r"[^a-zA-Z0-9]", "", clean)
            if clean_num.isdigit():
                return f"INV-{clean_num}"
            return clean.upper()

    @classmethod
    def _regex_fallback(
        cls, subject: str, body: str, raw_content: str | None = None
    ) -> dict[str, Any]:
        """Deterministic regex fallback parser when LLM is unavailable or offline."""
        logger.info("Executing regex/fallback triage parser")
        content = raw_content if raw_content else f"{subject}\n{body}"

        # 1. Extract invoice numbers (INV-XXXX)
        invoice_numbers = re.findall(r"\bINV-\d+\b", content, re.IGNORECASE)
        # De-duplicate while preserving order
        unique_invoices = list(dict.fromkeys([num.upper() for num in invoice_numbers]))

        # 2. Extract dispute categories based on keywords
        invoices_list = []
        for inv in unique_invoices:
            # Look for lines containing this invoice number
            inv_context = ""
            for line in content.splitlines():
                if inv in line.upper():
                    inv_context += line + "\n"

            # Fall back to entire content if no specific line matched
            if not inv_context:
                inv_context = content

            categories = []
            inv_context_lower = inv_context.lower()

            if any(
                kw in inv_context_lower
                for kw in [
                    "pricing",
                    "price",
                    "tax",
                    "quantity",
                    "wrong product",
                    "wrong customer",
                    "wrong total",
                    "wrong date",
                    "incorrect",
                    "charge",
                    "fee",
                    "rate",
                ]
            ):
                categories.append("AMENDMENT")

            if any(
                kw in inv_context_lower
                for kw in [
                    "already paid",
                    "payment done",
                    "paid yesterday",
                    "paid already",
                    "cleared",
                    "transaction",
                ]
            ):
                categories.append("PAYMENT_ALREADY_DONE")

            if any(
                kw in inv_context_lower
                for kw in [
                    "not reflected",
                    "missing payment",
                    "sent payment",
                    "bank transfer",
                    "wire",
                ]
            ):
                categories.append("PAYMENT_NOT_REFLECTED")

            if any(
                kw in inv_context_lower
                for kw in ["duplicate", "double bill", "twice", "copied"]
            ):
                categories.append("DUPLICATE_INVOICE")

            if any(
                kw in inv_context_lower
                for kw in [
                    "quality",
                    "damaged",
                    "broken",
                    "faulty",
                    "defect",
                    "poor",
                ]
            ):
                categories.append("QUALITY")

            if any(
                kw in inv_context_lower
                for kw in ["late", "delayed", "not arrived", "behind schedule"]
            ):
                categories.append("LATE_DELIVERY")

            if not categories:
                categories.append("OTHER")

            invoices_list.append(
                {
                    "invoice_number": inv,
                    "dispute_types": list(set(categories)),
                }
            )

        # Default fallback confidence is 75 (triggers human review queue since <80)
        return {
            "invoices": invoices_list,
            "confidence": 75,
        }

    @classmethod
    async def triage_communication(
        cls,
        subject: str,
        body: str,
        raw_content: str | None = None,
    ) -> dict[str, Any]:
        """Triages customer communication extracting invoices, categories, and confidence.

        Returns:
            Dict containing 'invoices', 'confidence', and 'agent_run_details'.
        """
        communication_text = (
            raw_content if raw_content else f"Subject: {subject}\nBody: {body}"
        )
        prompt = f"""You are a Dispute Triage Agent. Analyze the following customer communication (email subject, body, and any extracted attachment content) and extract all referenced invoices and the dispute reasons.

Communication Details:
{communication_text}

For each invoice mentioned, identify:
1. The invoice number (e.g. INV-1001, INV-2001).
2. The dispute category. You must map specific dispute reasons (PRICING, TAX, QUANTITY, WRONG_PRODUCT, WRONG_CUSTOMER, WRONG_TOTAL, WRONG_DATE) to the standard category "AMENDMENT". Other independent categories can be: "PAYMENT_ALREADY_DONE", "PAYMENT_NOT_REFLECTED", "DUPLICATE_INVOICE", "QUALITY", "LATE_DELIVERY", or "OTHER".

Determine your confidence score (0 to 100) for the extraction. If the invoice number or dispute reason is vague or hard to verify, provide a lower confidence score.

You must output a raw JSON object ONLY. No markdown wrapping (do not wrap in ```json ... ```), no explanations outside the JSON.

Expected JSON schema:
{{
  "invoices": [
    {{
      "invoice_number": "string",
      "dispute_types": ["string"]
    }}
  ],
  "confidence": number
}}
"""
        openrouter_key = settings.OPENROUTER_API_KEY
        gemini_key = settings.GEMINI_API_KEY

        models_to_try = [
            ("google/gemini-2.5-flash", "OpenRouter"),
            ("deepseek/deepseek-chat", "OpenRouter"),
            ("qwen/qwen-2.5-72b-instruct", "OpenRouter"),
        ]

        # Try OpenRouter models first
        if (
            openrouter_key
            and not openrouter_key.startswith("mock-")
            and openrouter_key.strip()
        ):
            for model, provider in models_to_try:
                start_time = time.time()
                try:
                    logger.info("Attempting triage with %s model: %s", provider, model)
                    headers = {
                        "Authorization": f"Bearer {openrouter_key.strip()}",
                        "Content-Type": "application/json",
                    }
                    payload = {
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.0,
                        "response_format": {"type": "json_object"},
                    }
                    async with httpx.AsyncClient() as client:
                        resp = await client.post(
                            "https://openrouter.ai/api/v1/chat/completions",
                            headers=headers,
                            json=payload,
                            timeout=15.0,
                        )
                        resp.raise_for_status()
                        result_json = resp.json()
                        content = result_json["choices"][0]["message"]["content"]

                    latency = time.time() - start_time
                    logger.info("Triage succeeded with model: %s", model)

                    # Parse output
                    parsed = cls._clean_and_parse_json(content)
                    return {
                        "invoices": cls._normalize_extracted_invoices(
                            parsed.get("invoices", [])
                        ),
                        "confidence": float(parsed.get("confidence", 90.0)),
                        "agent_run_details": {
                            "prompt": prompt,
                            "input_payload": {
                                "subject": subject,
                                "body": body,
                                "raw_content": raw_content,
                            },
                            "output_payload": parsed,
                            "latency": latency,
                            "model": model,
                            "provider": provider,
                            "status": "SUCCESS",
                        },
                    }
                except Exception as e:
                    logger.warning("Triage failed with model %s: %s", model, str(e))

        # Fallback to direct Gemini API
        if gemini_key and not gemini_key.startswith("mock-") and gemini_key.strip():
            start_time = time.time()
            model = settings.GEMINI_MODEL_NAME
            provider = "Direct Gemini"
            try:
                logger.info("Attempting triage with direct Gemini model: %s", model)
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={gemini_key.strip()}"
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "responseMimeType": "application/json",
                        "temperature": 0.0,
                    },
                }
                async with httpx.AsyncClient() as client:
                    resp = await client.post(url, json=payload, timeout=15.0)
                    resp.raise_for_status()
                    result_json = resp.json()
                    content = result_json["candidates"][0]["content"]["parts"][0][
                        "text"
                    ]

                latency = time.time() - start_time
                logger.info("Direct Gemini triage succeeded.")

                parsed = cls._clean_and_parse_json(content)
                return {
                    "invoices": cls._normalize_extracted_invoices(
                        parsed.get("invoices", [])
                    ),
                    "confidence": float(parsed.get("confidence", 90.0)),
                    "agent_run_details": {
                        "prompt": prompt,
                        "input_payload": {
                            "subject": subject,
                            "body": body,
                            "raw_content": raw_content,
                        },
                        "output_payload": parsed,
                        "latency": latency,
                        "model": model,
                        "provider": provider,
                        "status": "SUCCESS",
                    },
                }
            except Exception as e:
                logger.error("Direct Gemini triage failed: %s", str(e))

        # Deterministic fallback
        start_time = time.time()
        fallback_res = cls._regex_fallback(subject, body, raw_content)
        latency = time.time() - start_time

        return {
            "invoices": cls._normalize_extracted_invoices(fallback_res["invoices"]),
            "confidence": fallback_res["confidence"],
            "agent_run_details": {
                "prompt": prompt,
                "input_payload": {
                    "subject": subject,
                    "body": body,
                    "raw_content": raw_content,
                },
                "output_payload": fallback_res,
                "latency": latency,
                "model": "regex_fallback",
                "provider": "system",
                "status": "FALLBACK",
            },
        }

    @classmethod
    def _clean_and_parse_json(cls, content: str) -> dict[str, Any]:
        """Cleans potential markdown blocks and parses JSON string."""
        cleaned = content.strip()
        if cleaned.startswith("```"):
            # strip leading/trailing markdown blocks
            cleaned = re.sub(r"^```(?:json)?\n", "", cleaned)
            cleaned = re.sub(r"\n```$", "", cleaned)
        return json.loads(cleaned.strip())

    @classmethod
    def _normalize_extracted_invoices(
        cls, invoices: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Ensures all dispute types extracted are mapped/normalized correctly."""
        normalized = []
        for inv in invoices:
            raw_num = inv.get("invoice_number", "INV-UNKNOWN")
            invoice_num = cls.normalize_invoice_number(raw_num)
            types = inv.get("dispute_types", [])
            norm_types = list({cls.normalize_category(t) for t in types})
            if not norm_types:
                norm_types = ["OTHER"]
            normalized.append(
                {
                    "invoice_number": invoice_num,
                    "dispute_types": norm_types,
                }
            )
        return normalized
