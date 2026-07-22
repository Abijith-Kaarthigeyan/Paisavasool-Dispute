"""Late Delivery Agent evaluating lateness claims against PO promised date and GRN receipt dates."""

import json
import re
import time
from datetime import date, datetime
from typing import Any

from src.core.llm.llm_client import generate_text_completion
from src.observability.logging.logger import logger

VALID_OUTCOMES = frozenset(
    {
        "CUSTOMER_CORRECT",
        "COMPANY_CORRECT",
        "NEED_MORE_INFO",
        "ESCALATE_TO_LOGISTICS_TEAM",
    }
)

_DATE_PATTERNS = (
    re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"),
    re.compile(
        r"\b(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})\b",
    ),
    re.compile(
        r"\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
        r"\.?\s+\d{1,2},?\s+\d{4})\b",
        re.IGNORECASE,
    ),
)


class LateDeliveryAgent:
    """LLM-based agent evaluating late-delivery disputes against PO and GRN dates."""

    @classmethod
    def _parse_date(cls, value: Any) -> date | None:
        """Best-effort parse of a date string or date-like value."""
        if value is None:
            return None
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if isinstance(value, datetime):
            return value.date()

        text = str(value).strip()
        if not text:
            return None

        # ISO date or datetime prefix
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            pass

        for fmt in (
            "%Y-%m-%d",
            "%d/%m/%Y",
            "%m/%d/%Y",
            "%d-%m-%Y",
            "%d.%m.%Y",
            "%b %d, %Y",
            "%B %d, %Y",
            "%d %b %Y",
            "%d %B %Y",
        ):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        return None

    @classmethod
    def _extract_customer_promised_date(cls, raw_customer_text: str) -> date | None:
        """Pull a customer-stated promised/expected date when PO field is absent."""
        text = raw_customer_text or ""
        lower = text.lower()
        # Prefer clauses that mention promise / expected / committed delivery
        cue_window = re.search(
            r"(?:promis(?:ed|e)|expected|committed|scheduled|due|by|before)"
            r"[^\n.]{0,80}",
            lower,
        )
        search_region = (
            text[cue_window.start() : cue_window.end()] if cue_window else text
        )

        for pattern in _DATE_PATTERNS:
            match = pattern.search(search_region)
            if match:
                parsed = cls._parse_date(match.group(1))
                if parsed:
                    return parsed
        return None

    @classmethod
    def _resolve_promised_date(
        cls,
        raw_customer_text: str,
        purchase_order_json: dict | None,
    ) -> date | None:
        """Promised date = PO requested_delivery_date; else customer-stated; never invent."""
        po = purchase_order_json or {}
        requested = cls._parse_date(po.get("requested_delivery_date"))
        if requested:
            return requested
        return cls._extract_customer_promised_date(raw_customer_text)

    @classmethod
    def _resolve_actual_grn_date(cls, grns_json: list[dict]) -> date | None:
        """Use the latest parseable LINKED GRN grn_date as actual receipt."""
        dates: list[date] = []
        for grn in grns_json or []:
            parsed = cls._parse_date(grn.get("grn_date"))
            if parsed:
                dates.append(parsed)
        if not dates:
            return None
        return max(dates)

    @classmethod
    def _regex_fallback(
        cls,
        raw_customer_text: str,
        invoice_json: dict,
        purchase_order_json: dict | None,
        grns_json: list[dict],
    ) -> dict[str, Any]:
        """Deterministic fallback when LLM is offline — never confident auto-close."""
        logger.info("Executing regex/fallback late delivery resolution parser")

        if not grns_json:
            return {
                "resolution_outcome": "ESCALATE_TO_LOGISTICS_TEAM",
                "confidence": 40.0,
                "reasoning": (
                    "Fallback review: no linked goods receipt notes were available to "
                    "assess the late delivery claim. Escalating to the Logistics team."
                ),
            }

        promised = cls._resolve_promised_date(raw_customer_text, purchase_order_json)
        if not promised:
            return {
                "resolution_outcome": "NEED_MORE_INFO",
                "confidence": 45.0,
                "reasoning": (
                    "Fallback review: no promised delivery date was found on the purchase "
                    "order (requested_delivery_date) and none could be identified in the "
                    "customer message. Please confirm the date that was promised for delivery."
                ),
            }

        parseable = [
            g for g in grns_json if cls._parse_date(g.get("grn_date")) is not None
        ]
        if len(grns_json) > 1 and len(parseable) != len(grns_json):
            return {
                "resolution_outcome": "ESCALATE_TO_LOGISTICS_TEAM",
                "confidence": 40.0,
                "reasoning": (
                    "Fallback review: multiple goods receipt notes are linked but receipt "
                    "dates could not be fully parsed. Escalating to the Logistics team."
                ),
            }

        if len(parseable) > 1:
            late_flags = [
                cls._parse_date(g.get("grn_date")) > promised for g in parseable
            ]
            if any(late_flags) and not all(late_flags):
                return {
                    "resolution_outcome": "ESCALATE_TO_LOGISTICS_TEAM",
                    "confidence": 40.0,
                    "reasoning": (
                        "Fallback review: multiple linked goods receipt notes disagree on "
                        "whether delivery was late versus the promised date. Escalating to "
                        "the Logistics team."
                    ),
                }

        actual = cls._resolve_actual_grn_date(grns_json)
        if not actual:
            return {
                "resolution_outcome": "ESCALATE_TO_LOGISTICS_TEAM",
                "confidence": 40.0,
                "reasoning": (
                    "Fallback review: linked goods receipt notes were present but no "
                    "usable receipt date (grn_date) could be parsed. Escalating to the "
                    "Logistics team."
                ),
            }

        if actual > promised:
            return {
                "resolution_outcome": "CUSTOMER_CORRECT",
                "confidence": 55.0,
                "reasoning": (
                    f"Fallback review: goods were received on {actual.isoformat()}, which "
                    f"is after the promised delivery date of {promised.isoformat()}. "
                    "The late delivery claim appears supported pending associate review."
                ),
            }

        return {
            "resolution_outcome": "COMPANY_CORRECT",
            "confidence": 55.0,
            "reasoning": (
                f"Fallback review: goods were received on {actual.isoformat()}, which is "
                f"on or before the promised delivery date of {promised.isoformat()}. "
                "Records do not support a late delivery claim pending associate review."
            ),
        }

    @classmethod
    async def resolve_late_delivery(
        cls,
        raw_customer_text: str,
        invoice_json: dict,
        purchase_order_json: dict | None,
        grns_json: list[dict],
    ) -> dict[str, Any]:
        """Compares late-delivery claims against invoice, PO, and GRN evidence.

        Returns:
            Dict with resolution_outcome, confidence, reasoning, and agent_run_details.
        """
        prompt = f"""You are a Dispute Resolution Agent specialized in late delivery disputes.
Compare the customer's lateness claim against the invoice, purchase order, and goods receipt notes (GRNs).

This is a multi-turn conversation. Earlier messages establish the original claim; later messages may add clarification or documents. Read the FULL conversation chronologically before deciding.

Customer Conversation History (oldest to newest):
{raw_customer_text}

Invoice on file (system record):
{json.dumps(invoice_json, indent=2)}

Linked purchase order from system records:
{json.dumps(purchase_order_json, indent=2) if purchase_order_json is not None else "null"}

Linked goods receipt notes (GRNs) from system records (status LINKED only):
{json.dumps(grns_json, indent=2)}

DECISION RULES (apply in order):

1. Promised / requested delivery date:
   - Primary: purchase order field "requested_delivery_date".
   - Backup ONLY when that field is null/missing: a date the customer clearly stated as the promised, expected, or committed delivery date.
   - NEVER invent a promised date (do not derive one from po_date, invoice_date, or "po_date + N days").

2. Actual delivery / receipt date:
   - Use the relevant LINKED GRN "grn_date" (prefer the GRN most relevant to the claim; if multiple clean receipts, use the latest receipt date as actual delivery).
   - Only LINKED GRNs in the provided list may be used.

3. Lateness comparison:
   - If grn_date > promised/requested date → CUSTOMER_CORRECT (delivery was late).
   - If grn_date is on or before promised/requested date → COMPANY_CORRECT (not late per records).

4. Outcomes — choose exactly one:
   - CUSTOMER_CORRECT: Receipt date is after the promised/requested date.
   - COMPANY_CORRECT: Receipt date is on or before the promised/requested date.
   - NEED_MORE_INFO: No promised/requested date is available on the PO and the customer did not clearly state one — ask specifically for the promised delivery date.
   - ESCALATE_TO_LOGISTICS_TEAM: No usable LINKED GRNs, conflicting multi-GRN evidence, unparseable dates, or logistics judgment beyond a simple date compare.

5. Caution:
   - Never invent GRN dates or promised dates not present in the records/conversation.
   - Do not auto-close with high confidence when dates are ambiguous — prefer NEED_MORE_INFO or ESCALATE_TO_LOGISTICS_TEAM.
   - Confidence should reflect evidence strength (0.0 to 100.0).

The "reasoning" field is shown to AR associates. Use plain business language:
- Say "goods receipt note", "purchase order", "requested delivery date", "invoice on file" — never "JSON".
- Cite GRN numbers, requested vs receipt dates when explaining.

You must output a raw JSON object ONLY. Do not wrap in markdown code blocks.
Expected JSON Schema:
{{
  "resolution_outcome": "CUSTOMER_CORRECT" | "COMPANY_CORRECT" | "NEED_MORE_INFO" | "ESCALATE_TO_LOGISTICS_TEAM",
  "confidence": number,
  "reasoning": "string"
}}
"""
        completion = await generate_text_completion(
            prompt=prompt,
            temperature=0.0,
            agent_label="late delivery",
        )
        input_payload = {
            "raw_customer_text": raw_customer_text,
            "invoice_json": invoice_json,
            "purchase_order_json": purchase_order_json,
            "grns_json": grns_json,
        }

        if completion:
            parsed = cls._clean_and_parse_json(completion.content)
            outcome = parsed.get("resolution_outcome", "ESCALATE_TO_LOGISTICS_TEAM")
            if outcome not in VALID_OUTCOMES:
                outcome = "ESCALATE_TO_LOGISTICS_TEAM"
            return {
                "resolution_outcome": outcome,
                "confidence": float(parsed.get("confidence", 70.0)),
                "reasoning": parsed.get("reasoning", ""),
                "agent_run_details": {
                    "prompt": prompt,
                    "input_payload": input_payload,
                    "output_payload": parsed,
                    "latency": completion.latency,
                    "model": completion.model,
                    "provider": completion.provider,
                    "status": "SUCCESS",
                },
            }

        start_time = time.time()
        fallback_res = cls._regex_fallback(
            raw_customer_text,
            invoice_json,
            purchase_order_json,
            grns_json,
        )
        latency = time.time() - start_time

        return {
            "resolution_outcome": fallback_res["resolution_outcome"],
            "confidence": fallback_res["confidence"],
            "reasoning": fallback_res["reasoning"],
            "agent_run_details": {
                "prompt": prompt,
                "input_payload": input_payload,
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
            cleaned = re.sub(r"^```(?:json)?\n", "", cleaned)
            cleaned = re.sub(r"\n```$", "", cleaned)
        return json.loads(cleaned.strip())
