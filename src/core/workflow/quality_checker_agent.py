"""Quality Checker Agent evaluating quality claims against invoice, PO, and GRN evidence."""

import json
import re
import time
from typing import Any

from src.core.llm.llm_client import generate_text_completion
from src.observability.logging.logger import logger

VALID_OUTCOMES = frozenset(
    {
        "CUSTOMER_CORRECT",
        "COMPANY_CORRECT",
        "NEED_MORE_INFO",
        "ESCALATE_TO_QUALITY_TEAM",
    }
)


class QualityCheckerAgent:
    """LLM-based agent evaluating quality disputes against GRN and PO evidence."""

    @classmethod
    def _regex_fallback(
        cls,
        raw_customer_text: str,
        invoice_json: dict,
        purchase_order_json: dict | None,
        grns_json: list[dict],
    ) -> dict[str, Any]:
        """Deterministic fallback when LLM is offline — never auto-close confidently."""
        logger.info("Executing regex/fallback quality resolution parser")

        if not grns_json:
            return {
                "resolution_outcome": "ESCALATE_TO_QUALITY_TEAM",
                "confidence": 40.0,
                "reasoning": (
                    "Fallback review: no linked goods receipt notes were available to "
                    "assess the quality claim. Escalating to the Quality team."
                ),
            }

        text_lower = (raw_customer_text or "").lower()
        damage_keywords = (
            "damaged",
            "broken",
            "defect",
            "faulty",
            "quality",
            "spoiled",
            "shortage",
            "missing",
        )
        mentions_quality = any(k in text_lower for k in damage_keywords)

        if mentions_quality:
            return {
                "resolution_outcome": "NEED_MORE_INFO",
                "confidence": 45.0,
                "reasoning": (
                    "Fallback review: a quality concern was raised but automated "
                    "evidence matching is unavailable. Please share photos, batch/lot "
                    "details, and which line items or quantities are affected."
                ),
            }

        return {
            "resolution_outcome": "ESCALATE_TO_QUALITY_TEAM",
            "confidence": 35.0,
            "reasoning": (
                "Fallback review: unable to confidently assess the quality claim "
                "against GRN evidence without LLM. Escalating to the Quality team."
            ),
        }

    @classmethod
    async def resolve_quality(
        cls,
        raw_customer_text: str,
        invoice_json: dict,
        purchase_order_json: dict | None,
        grns_json: list[dict],
    ) -> dict[str, Any]:
        """Compares quality claims against invoice, PO, and GRN evidence.

        Returns:
            Dict with resolution_outcome, confidence, reasoning, and agent_run_details.
        """
        prompt = f"""You are a Dispute Resolution Agent specialized in product quality disputes.
Compare the customer's quality claim against the invoice, purchase order, and goods receipt notes (GRNs).

This is a multi-turn conversation. Earlier messages establish the original claim; later messages may add photos, clarification, or documents. Read the FULL conversation chronologically before deciding.

Customer Conversation History (oldest to newest):
{raw_customer_text}

Invoice on file (system record):
{json.dumps(invoice_json, indent=2)}

Linked purchase order from system records:
{json.dumps(purchase_order_json, indent=2) if purchase_order_json is not None else "null"}

Linked goods receipt notes (GRNs) from system records (status LINKED only):
{json.dumps(grns_json, indent=2)}

DECISION RULES (apply in order):

1. Evidence chain:
   - Use invoice + PO + GRN(s) together. Do not decide from GRN alone.
   - Select the GRN(s) most relevant by receipt date, line items/quantities, and notes relative to the claim.
   - GRN notes mentioning damage, shortage, rejection, or quality issues are strong supporting evidence for the customer when they align with the claim.
   - Clean GRNs (no damage/shortage notes, quantities match PO/invoice) support the company when the claim alleges receipt-time quality problems with no other supporting evidence.

2. Outcomes — choose exactly one:
   - CUSTOMER_CORRECT: Evidence (especially GRN notes/lines) supports the customer's quality claim.
   - COMPANY_CORRECT: Evidence does not support the quality claim; receipt records are consistent with a clean delivery matching the PO/invoice.
   - NEED_MORE_INFO: Claim is plausible but essential customer evidence is still missing (e.g. photos of damage, which SKU/lot, quantity affected). Ask for specific missing items in reasoning.
   - ESCALATE_TO_QUALITY_TEAM: Evidence is incomplete, conflicting, or requires specialized QC/RMA judgment that associates cannot resolve from these records alone.

3. Caution:
   - Never invent GRN findings that are not in the provided records.
   - Do not auto-close with high confidence when GRN notes are empty and the claim is vague — prefer NEED_MORE_INFO or ESCALATE_TO_QUALITY_TEAM.
   - Confidence should reflect evidence strength (0.0 to 100.0).

The "reasoning" field is shown to AR associates. Use plain business language:
- Say "goods receipt note", "purchase order", "invoice on file" — never "JSON".
- Cite GRN numbers, dates, quantities, and note excerpts when explaining.

You must output a raw JSON object ONLY. Do not wrap in markdown code blocks.
Expected JSON Schema:
{{
  "resolution_outcome": "CUSTOMER_CORRECT" | "COMPANY_CORRECT" | "NEED_MORE_INFO" | "ESCALATE_TO_QUALITY_TEAM",
  "confidence": number,
  "reasoning": "string"
}}
"""
        completion = await generate_text_completion(
            prompt=prompt,
            temperature=0.0,
            agent_label="quality checker",
        )
        input_payload = {
            "raw_customer_text": raw_customer_text,
            "invoice_json": invoice_json,
            "purchase_order_json": purchase_order_json,
            "grns_json": grns_json,
        }

        if completion:
            parsed = cls._clean_and_parse_json(completion.content)
            outcome = parsed.get("resolution_outcome", "ESCALATE_TO_QUALITY_TEAM")
            if outcome not in VALID_OUTCOMES:
                outcome = "ESCALATE_TO_QUALITY_TEAM"
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
