"""Amendment Resolution Agent with fallback LLM/OpenRouter and regex processing."""

import json
import re
import time
from typing import Any

from src.core.llm.llm_client import generate_text_completion
from src.observability.logging.logger import logger


class AmendmentResolutionAgent:
    """LLM-based agent evaluating customer claims against invoice details."""

    @classmethod
    def _regex_fallback(
        cls, raw_customer_text: str, invoice_json: dict
    ) -> dict[str, Any]:
        """Deterministic fallback when LLM is offline or key missing."""
        logger.info("Executing regex/fallback amendment resolution parser")

        # Simple heuristic fallback
        text_lower = raw_customer_text.lower()

        # Default decision is to ask for clarification
        outcome = "NEED_MORE_INFO"
        confidence = 75.0
        reasoning = "Fallback regex active. No LLM response. Requesting clarification on claims."
        recommended_invoice = None

        # Check if they request simple price change or tax check
        if "pricing" in text_lower or "price" in text_lower:
            outcome = "NEED_MORE_INFO"
            reasoning = "Customer claims pricing error. Need manual associate verification of contract prices. (fallback)"
        elif "tax" in text_lower:
            outcome = "NEED_MORE_INFO"
            reasoning = "Customer claims tax calculation is incorrect. Reviewing tax rules. (fallback)"

        return {
            "resolution_outcome": outcome,
            "confidence": confidence,
            "reasoning": reasoning,
            "recommended_invoice_json": recommended_invoice,
        }

    @classmethod
    async def resolve_amendment(
        cls,
        raw_customer_text: str,
        invoice_json: dict,
        dispute_category: str,
    ) -> dict[str, Any]:
        """Compares customer claims against invoice and decides CORRECT/INCORRECT/NEED_MORE_INFO.

        Returns:
            Dict containing outcome, confidence, reasoning, recommended_invoice_json, and agent_run_details.
        """
        prompt = f"""You are a Dispute Resolution Agent specialized in invoice amendments.
Compare the customer's claims/complaint against the invoice details and category to determine if the customer is correct, company is correct, or if we need more info.

This is a multi-turn conversation. Earlier messages establish the customer's original dispute claim; later messages may provide additional documents or clarification requested by our team. Read the FULL conversation chronologically before deciding.

Dispute Category: {dispute_category}
Customer Conversation History (oldest to newest):
{raw_customer_text}

Invoice on file (system record):
{json.dumps(invoice_json, indent=2)}

DECISION RULES (apply in order; these override general caution):

1. Purchase order / contract matching (billing claims):
   - If the customer provided a purchase order (PO), contract, or similar supporting document in the conversation, compare it against the invoice on file.
   - When line items, quantities, unit rates/prices, subtotal, tax rate, and tax amount on the invoice align with the PO/contract, the billing dispute is resolved: choose COMPANY_CORRECT for those billing claims.
   - Do NOT choose NEED_MORE_INFO and do NOT ask for additional tax, pricing, or line-item documentation when the PO/contract and invoice already agree on those fields.

2. When to use NEED_MORE_INFO (billing only):
   - Use NEED_MORE_INFO only when essential evidence is still missing AND the invoice on file cannot be validated against documents the customer has already provided.
   - Examples: no PO/contract was supplied and amounts cannot be verified; customer-provided document is illegible or incomplete; disputed fields cannot be compared because key values are absent from both invoice and supplied documents.
   - Do NOT use NEED_MORE_INFO merely because the customer disagrees — if their supplied PO/contract supports the invoice as issued, that is COMPANY_CORRECT.

3. Multi-issue disputes (billing + non-billing, e.g. tax/pricing AND late delivery):
   - Evaluate billing claims (tax, pricing, quantity, line items) separately from operational claims (late delivery, quality, receipt date).
   - If billing claims are verified as COMPANY_CORRECT against the PO/contract and invoice, choose COMPANY_CORRECT even when a separate operational issue (such as missing delivery/receipt date) cannot yet be verified.
   - In reasoning, state clearly that billing was reviewed and matches the PO/contract; note any unresolved operational concern without requesting duplicate billing documentation.
   - Do not withhold COMPANY_CORRECT on billing because an unrelated delivery or quality detail is still outstanding.

You must determine one of the following resolution outcomes:
- CUSTOMER_CORRECT: The customer's claim is valid and supported by the invoice details or obvious errors. You MUST generate a "recommended_invoice_json" reflecting the corrected subtotal, tax, total, and items.
- COMPANY_CORRECT: The original invoice is correct and the customer's claim is invalid or incorrect, including when a provided PO/contract confirms the invoice as issued. Set "recommended_invoice_json" to null.
- NEED_MORE_INFO: Essential billing evidence is still missing and the invoice cannot be validated against documents already provided. Set "recommended_invoice_json" to null. Do not use this outcome when PO/contract and invoice already agree on disputed billing fields.

Provide a confidence score (0.0 to 100.0) and detailed reasoning.
The "reasoning" field is shown directly to AR associates in the product UI. Use plain business language only:
- Say "invoice on file" or "company records", never "invoice JSON" or "JSON".
- Say "documents provided by the customer" for customer-supplied files.
- Cite specific amounts, tax rates, and percentages when explaining discrepancies.
Only generate "recommended_invoice_json" if the resolution_outcome is "CUSTOMER_CORRECT". Otherwise it must be null.

When generating "recommended_invoice_json", include the FULL corrected invoice using the same structure as the source invoice JSON:
- invoice_number
- customer_name
- invoice_date (issue date)
- due_date
- subtotal_amount
- tax_amount
- total_amount
- outstanding_amount (if applicable)
- items: array of line items, each with description/product name, quantity, unit_price, and line amount

Copy unchanged fields from the original invoice JSON when they are not being amended.

You must output a raw JSON object ONLY. Do not wrap in markdown code blocks.
Expected JSON Schema:
{{
  "resolution_outcome": "CUSTOMER_CORRECT" | "COMPANY_CORRECT" | "NEED_MORE_INFO",
  "confidence": number,
  "reasoning": "string",
  "recommended_invoice_json": {{
    "invoice_number": "string",
    "customer_name": "string",
    "invoice_date": "string",
    "due_date": "string",
    "subtotal_amount": number,
    "tax_amount": number,
    "total_amount": number,
    "outstanding_amount": number,
    "items": [
      {{
        "description": "string",
        "quantity": number,
        "unit_price": number,
        "amount": number
      }}
    ]
  }} | null
}}
"""
        completion = await generate_text_completion(
            prompt=prompt,
            temperature=0.0,
            agent_label="amendment resolution",
        )
        if completion:
            parsed = cls._clean_and_parse_json(completion.content)
            return {
                "resolution_outcome": parsed.get(
                    "resolution_outcome", "NEED_MORE_INFO"
                ),
                "confidence": float(parsed.get("confidence", 90.0)),
                "reasoning": parsed.get("reasoning", ""),
                "recommended_invoice_json": parsed.get("recommended_invoice_json")
                if parsed.get("resolution_outcome") == "CUSTOMER_CORRECT"
                else None,
                "agent_run_details": {
                    "prompt": prompt,
                    "input_payload": {
                        "raw_customer_text": raw_customer_text,
                        "invoice_json": invoice_json,
                    },
                    "output_payload": parsed,
                    "latency": completion.latency,
                    "model": completion.model,
                    "provider": completion.provider,
                    "status": "SUCCESS",
                },
            }

        # Regex Fallback
        start_time = time.time()
        fallback_res = cls._regex_fallback(raw_customer_text, invoice_json)
        latency = time.time() - start_time

        return {
            "resolution_outcome": fallback_res["resolution_outcome"],
            "confidence": fallback_res["confidence"],
            "reasoning": fallback_res["reasoning"],
            "recommended_invoice_json": fallback_res["recommended_invoice_json"],
            "agent_run_details": {
                "prompt": prompt,
                "input_payload": {
                    "raw_customer_text": raw_customer_text,
                    "invoice_json": invoice_json,
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
            cleaned = re.sub(r"^```(?:json)?\n", "", cleaned)
            cleaned = re.sub(r"\n```$", "", cleaned)
        return json.loads(cleaned.strip())
