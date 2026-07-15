"""Payment Reference Extraction Agent with fallback LLM/OpenRouter and regex processing."""

import json
import re
import time
from typing import Any

from src.core.llm.llm_client import generate_text_completion
from src.observability.logging.logger import logger


class PaymentReferenceExtractionAgent:
    """LLM-based agent extracting payment transaction references from customer texts."""

    @classmethod
    def _regex_fallback(cls, raw_customer_text: str) -> dict[str, Any]:
        """Deterministic fallback regex matcher when LLM is unavailable."""
        logger.info("Executing regex/fallback payment reference extractor")

        # 1. Search for common UTR patterns like 12-22 digit numbers or alphanumeric strings
        # E.g. SBI/HDFC/ICICI UTRs are often 12-16 digit numbers or specific alpha formats
        utr_match = re.search(r"\b[A-Z0-9]{12,22}\b", raw_customer_text, re.IGNORECASE)
        if utr_match:
            return {"reference_number": utr_match.group(0).upper(), "confidence": 85.0}

        # Try generic reference pattern if found
        tx_match = re.search(
            r"(?:tx|txn|ref|reference|utr)[:\-\s#]+([A-Z0-9]+)",
            raw_customer_text,
            re.IGNORECASE,
        )
        if tx_match:
            return {"reference_number": tx_match.group(1).upper(), "confidence": 80.0}

        return {"reference_number": None, "confidence": 100.0}

    @classmethod
    async def extract_reference(cls, raw_customer_text: str) -> dict[str, Any]:
        """Extracts UTR / payment reference from customer text.

        Returns:
            Dict containing reference_number, confidence, and agent_run_details.
        """
        prompt = f"""You are a Payment Reference Extraction Agent.
Analyze the customer's text (email subject, body, or comments) and extract any payment reference number.
This includes: UTR (Unique Transaction Reference), Bank Reference Number, Transaction ID, Payment reference, or Wire reference.

Customer Text:
{raw_customer_text}

Determine your confidence score (0.0 to 100.0) for the extraction.
If no reference number is mentioned, return null for reference_number.

You must output a raw JSON object ONLY. Do not wrap in markdown code blocks.
Expected JSON Schema:
{{
  "reference_number": "string" | null,
  "confidence": number
}}
"""
        completion = await generate_text_completion(
            prompt=prompt,
            temperature=0.0,
            agent_label="payment reference extraction",
        )
        if completion:
            parsed = cls._clean_and_parse_json(completion.content)
            return {
                "reference_number": parsed.get("reference_number"),
                "confidence": float(parsed.get("confidence", 90.0)),
                "agent_run_details": {
                    "prompt": prompt,
                    "input_payload": {"raw_customer_text": raw_customer_text},
                    "output_payload": parsed,
                    "latency": completion.latency,
                    "model": completion.model,
                    "provider": completion.provider,
                    "status": "SUCCESS",
                },
            }

        # Regex Fallback
        start_time = time.time()
        fallback_res = cls._regex_fallback(raw_customer_text)
        latency = time.time() - start_time

        return {
            "reference_number": fallback_res["reference_number"],
            "confidence": fallback_res["confidence"],
            "agent_run_details": {
                "prompt": prompt,
                "input_payload": {"raw_customer_text": raw_customer_text},
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
