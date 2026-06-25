"""Dispute Mail Agent generating customer-facing communications and internal notifications."""

import json
import re
import time
from typing import Any

import httpx

from src.core.config.settings import settings
from src.observability.logging.logger import logger


class DisputeMailAgent:
    """LLM-based mail generator crafting dispute resolution email communications."""

    @classmethod
    def _template_fallback(
        cls,
        dispute_category: str,
        outcome: str,
        customer_email: str,
        info_request: str | None = None,
        invoice_number: str | None = None,
    ) -> dict[str, Any]:
        """Provides high-quality, structured fallback templates for emails when LLM is unavailable."""
        logger.info("Executing template fallback email generator")

        recipient = customer_email
        subject = f"Paisa Vasool Update on your Dispute - {dispute_category}"

        if outcome == "CUSTOMER_CORRECT":
            body = (
                "Dear Customer,\n\n"
                "We have completed the review of your dispute. Your concern has been validated, "
                "and we have approved the proposed resolution. The necessary adjustments have been "
                "applied to your invoice.\n\n"
                "Thank you for your patience and for bringing this to our attention.\n\n"
                "Best regards,\n"
                "Paisa Vasool Customer Support Team"
            )
        elif outcome == "COMPANY_CORRECT":
            body = (
                "Dear Customer,\n\n"
                "We have completed our investigation regarding your dispute. Based on our records and system validation, "
                "the original invoice is correct as issued. No amendments are recommended at this time.\n\n"
                "Should you have any further questions or require copies of relevant documents, please let us know.\n\n"
                "Best regards,\n"
                "Paisa Vasool Customer Support Team"
            )
        elif outcome == "NEED_MORE_INFO":
            request_text = (
                info_request
                or "We require additional information to complete our investigation."
            )
            if invoice_number:
                invoice_prompt = (
                    f"Please reply with the requested details along with your "
                    f"invoice number ({invoice_number})."
                )
            else:
                invoice_prompt = "Please reply with the requested details along with your invoice number."
            body = (
                "Dear Customer,\n\n"
                f"{request_text}\n\n"
                f"{invoice_prompt}\n\n"
                "Once received, we will resume the validation immediately.\n\n"
                "Best regards,\n"
                "Paisa Vasool Customer Support Team"
            )
        elif outcome == "OPERATIONAL_ESCALATION" or "WAITING_INTERNAL" in outcome:
            body = (
                "Dear Customer,\n\n"
                "Your dispute has been escalated to the relevant operations department for a detailed review "
                "of quality or delivery parameters. We will notify you as soon as the internal review is completed.\n\n"
                "Thank you for your patience.\n\n"
                "Best regards,\n"
                "Paisa Vasool Operational Support Team"
            )
        else:
            body = (
                "Dear Customer,\n\n"
                "We are writing to update you that your dispute is currently under review by our billing team. "
                "We will reach out to you if we require additional details or once a final decision is reached.\n\n"
                "Best regards,\n"
                "Paisa Vasool Customer Support Team"
            )

        return {"recipient": recipient, "subject": subject, "body": body}

    @classmethod
    async def generate_mail(
        cls,
        dispute_category: str,
        outcome: str,
        customer_email: str,
        activities_summary: str,
        comments_summary: str,
        invoice_summary: str,
        info_request: str | None = None,
        invoice_number: str | None = None,
    ) -> dict[str, Any]:
        """Generates customized email subject and body for dispute updates using LLM or fallback.

        Returns:
            Dict containing subject, body, recipient, and agent_run_details.
        """
        prompt = f"""You are a professional Dispute Mail Agent for Paisa Vasool.
Your goal is to generate a customer-facing email notification about their dispute resolution status.

Dispute Category: {dispute_category}
Dispute Outcome: {outcome}
Customer Email: {customer_email}

Context Activities:
{activities_summary}

Context Comments:
{comments_summary}

Invoice Details:
{invoice_summary}

Information Requested from Customer:
{info_request or "Additional details needed to proceed."}

Invoice Number on Record:
{invoice_number or "Not specified"}

Based on the outcome, draft a professional, polite customer support email:
- CUSTOMER_CORRECT: Inform them that we validated their concern and adjusted details, thanking them.
- COMPANY_CORRECT: Politely explain that records show the original invoice is correct.
- NEED_MORE_INFO: Clearly explain what information is needed using the "Information Requested from Customer" field above. Always ask the customer to reply with the requested details along with their invoice number.
- OPERATIONAL_ESCALATION (or internal review): Notify them that we escalated the case to the operations department for review.

Output a raw JSON object ONLY. Do not wrap in markdown code blocks.
Expected JSON Schema:
{{
  "recipient": "string",
  "subject": "string",
  "body": "string"
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
                        "Attempting mail generation with %s model: %s", provider, model
                    )
                    headers = {
                        "Authorization": f"Bearer {openrouter_key.strip()}",
                        "Content-Type": "application/json",
                    }
                    payload = {
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.3,
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
                    logger.info("Mail generation succeeded with model: %s", model)

                    parsed = cls._clean_and_parse_json(content)
                    return {
                        "recipient": parsed.get("recipient", customer_email),
                        "subject": parsed.get(
                            "subject", f"Update on your dispute - {dispute_category}"
                        ),
                        "body": parsed.get("body", ""),
                        "agent_run_details": {
                            "prompt": prompt,
                            "input_payload": {
                                "category": dispute_category,
                                "outcome": outcome,
                                "email": customer_email,
                            },
                            "output_payload": parsed,
                            "latency": latency,
                            "model": model,
                            "provider": provider,
                            "status": "SUCCESS",
                        },
                    }
                except Exception as e:
                    logger.warning(
                        "Mail generation failed with model %s: %s", model, str(e)
                    )

        if gemini_key and not gemini_key.startswith("mock-") and gemini_key.strip():
            start_time = time.time()
            model = settings.GEMINI_MODEL_NAME
            provider = "Direct Gemini"
            try:
                logger.info(
                    "Attempting mail generation with direct Gemini model: %s", model
                )
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={gemini_key.strip()}"
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "responseMimeType": "application/json",
                        "temperature": 0.3,
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
                logger.info("Direct Gemini mail generation succeeded.")

                parsed = cls._clean_and_parse_json(content)
                return {
                    "recipient": parsed.get("recipient", customer_email),
                    "subject": parsed.get(
                        "subject", f"Update on your dispute - {dispute_category}"
                    ),
                    "body": parsed.get("body", ""),
                    "agent_run_details": {
                        "prompt": prompt,
                        "input_payload": {
                            "category": dispute_category,
                            "outcome": outcome,
                            "email": customer_email,
                        },
                        "output_payload": parsed,
                        "latency": latency,
                        "model": model,
                        "provider": provider,
                        "status": "SUCCESS",
                    },
                }
            except Exception as e:
                logger.error("Direct Gemini mail generation failed: %s", str(e))

        # Template Fallback
        start_time = time.time()
        fallback_res = cls._template_fallback(
            dispute_category,
            outcome,
            customer_email,
            info_request=info_request,
            invoice_number=invoice_number,
        )
        latency = time.time() - start_time

        return {
            "recipient": fallback_res["recipient"],
            "subject": fallback_res["subject"],
            "body": fallback_res["body"],
            "agent_run_details": {
                "prompt": prompt,
                "input_payload": {
                    "category": dispute_category,
                    "outcome": outcome,
                    "email": customer_email,
                },
                "output_payload": fallback_res,
                "latency": latency,
                "model": "template_fallback",
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
