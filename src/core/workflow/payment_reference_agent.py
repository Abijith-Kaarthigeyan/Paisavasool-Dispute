"""Payment Reference Extraction Agent with fallback LLM/OpenRouter and regex processing."""

import json
import re
import time
from typing import Any

import httpx

from src.core.config.settings import settings
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
        openrouter_key = settings.OPENROUTER_API_KEY
        gemini_key = settings.GEMINI_API_KEY

        models_to_try = [
            ("google/gemini-2.5-flash", "OpenRouter"),
            ("deepseek/deepseek-chat", "OpenRouter"),
            ("qwen/qwen-2.5-72b-instruct", "OpenRouter"),
        ]

        if (
            openrouter_key
            and not openrouter_key.startswith("mock-")
            and openrouter_key.strip()
        ):
            for model, provider in models_to_try:
                start_time = time.time()
                try:
                    logger.info(
                        "Attempting payment reference extraction with %s model: %s",
                        provider,
                        model,
                    )
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
                    logger.info(
                        "Payment reference extraction succeeded with model: %s", model
                    )

                    parsed = cls._clean_and_parse_json(content)
                    return {
                        "reference_number": parsed.get("reference_number"),
                        "confidence": float(parsed.get("confidence", 90.0)),
                        "agent_run_details": {
                            "prompt": prompt,
                            "input_payload": {"raw_customer_text": raw_customer_text},
                            "output_payload": parsed,
                            "latency": latency,
                            "model": model,
                            "provider": provider,
                            "status": "SUCCESS",
                        },
                    }
                except Exception as e:
                    logger.warning(
                        "Payment reference extraction failed with model %s: %s",
                        model,
                        str(e),
                    )

        if gemini_key and not gemini_key.startswith("mock-") and gemini_key.strip():
            start_time = time.time()
            model = settings.GEMINI_MODEL_NAME
            provider = "Direct Gemini"
            try:
                logger.info(
                    "Attempting payment reference extraction with direct Gemini model: %s",
                    model,
                )
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
                logger.info("Direct Gemini payment reference extraction succeeded.")

                parsed = cls._clean_and_parse_json(content)
                return {
                    "reference_number": parsed.get("reference_number"),
                    "confidence": float(parsed.get("confidence", 90.0)),
                    "agent_run_details": {
                        "prompt": prompt,
                        "input_payload": {"raw_customer_text": raw_customer_text},
                        "output_payload": parsed,
                        "latency": latency,
                        "model": model,
                        "provider": provider,
                        "status": "SUCCESS",
                    },
                }
            except Exception as e:
                logger.error(
                    "Direct Gemini payment reference extraction failed: %s", str(e)
                )

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
