"""Dispute Mail Agent generating customer-facing communications and internal notifications."""

import json
import re
import time
from typing import Any

import httpx

from src.core.config.settings import settings
from src.observability.logging.logger import logger

PAYMENT_CATEGORIES = frozenset({"PAYMENT_ALREADY_DONE", "PAYMENT_NOT_REFLECTED"})


class DisputeMailAgent:
    """LLM-based mail generator crafting dispute resolution email communications."""

    @staticmethod
    def _is_payment_category(dispute_category: str) -> bool:
        return dispute_category in PAYMENT_CATEGORIES

    @staticmethod
    def _truncate(text: str | None, limit: int = 1200) -> str:
        if not text:
            return ""
        cleaned = text.strip()
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[: limit - 3] + "..."

    @classmethod
    def _build_outcome_guidance(
        cls,
        dispute_category: str,
        outcome: str,
        *,
        info_request: str | None,
        payment_reference: str | None,
        resolution_reason: str | None,
    ) -> str:
        """Returns outcome instructions tailored to dispute category."""
        if cls._is_payment_category(dispute_category):
            if outcome == "CUSTOMER_CORRECT":
                ref_note = (
                    f" Mention payment reference {payment_reference} if provided."
                    if payment_reference
                    else ""
                )
                return (
                    "CUSTOMER_CORRECT (payment dispute): Confirm that the customer's payment "
                    "was found in our records and has been (or will be) applied to the invoice. "
                    "Explain the updated payment status clearly. Do NOT say invoice line items "
                    f"were wrong.{ref_note}"
                )
            if outcome == "COMPANY_CORRECT":
                return (
                    "COMPANY_CORRECT (payment dispute): Explain that after reviewing bank/payment "
                    "records we could NOT verify a settled payment matching the customer's claim "
                    "for this invoice. State that the outstanding balance remains unchanged. "
                    "Do NOT say 'invoice details are correct' or discuss billing line items — "
                    "the issue is payment settlement, not invoice accuracy. Offer to re-check if "
                    "they share UTR/reference, payment date, amount, and remitter details."
                )
            if outcome == "NEED_MORE_INFO":
                return (
                    "NEED_MORE_INFO (payment dispute): Ask specifically for UTR/transaction "
                    "reference, payment date, amount paid, and remitting bank account. Use the "
                    "Information Requested field. Request invoice number in the reply."
                )
            return (
                "Payment dispute update: Describe the current payment verification status in "
                "plain language tied to the customer's concern."
            )

        if dispute_category == "AMENDMENT":
            if outcome == "CUSTOMER_CORRECT":
                return (
                    "CUSTOMER_CORRECT (amendment): Confirm the billing concern was valid and "
                    "describe the adjustment or credit applied to the invoice."
                )
            if outcome == "COMPANY_CORRECT":
                return (
                    "COMPANY_CORRECT (amendment): Explain that invoice charges were reviewed "
                    "against contract/records and remain correct as issued."
                )
            if outcome == "NEED_MORE_INFO":
                return (
                    "NEED_MORE_INFO (amendment): Ask for the specific line item, amount, or "
                    "document supporting the requested change."
                )

        if outcome == "CUSTOMER_CORRECT":
            return (
                "CUSTOMER_CORRECT: Acknowledge the customer's concern was validated and "
                "summarize the corrective action taken."
            )
        if outcome == "COMPANY_CORRECT":
            return (
                "COMPANY_CORRECT: Politely explain our review conclusion and why records support "
                "the current invoice as issued."
            )
        if outcome == "NEED_MORE_INFO":
            request = info_request or "additional supporting details"
            return (
                f"NEED_MORE_INFO: Clearly request: {request}. Ask the customer to reply with "
                "those details and their invoice number."
            )
        if outcome in {"OPERATIONAL_ESCALATION", "PENDING_INTERNAL_REVIEW"} or (
            "WAITING_INTERNAL" in outcome
        ):
            return (
                "Internal escalation: Inform the customer the case was forwarded to the "
                "relevant operations team and we will follow up after review."
            )
        return "Provide a clear status update aligned with the outcome and customer message."

    @classmethod
    def _compose_prompt(
        cls,
        *,
        dispute_category: str,
        outcome: str,
        customer_email: str,
        activities_summary: str,
        comments_summary: str,
        invoice_summary: str,
        info_request: str | None,
        invoice_number: str | None,
        customer_message_summary: str | None,
        payment_reference: str | None,
        resolution_reason: str | None,
    ) -> str:
        outcome_guidance = cls._build_outcome_guidance(
            dispute_category,
            outcome,
            info_request=info_request,
            payment_reference=payment_reference,
            resolution_reason=resolution_reason,
        )
        customer_msg = cls._truncate(customer_message_summary) or "Not available."
        reason_line = resolution_reason or "Not specified."

        return f"""You are a professional accounts-receivable support writer for Paisa Vasool.
Draft a customer-facing email about a dispute update.

RULES (must follow):
1. Address the customer's ACTUAL concern from their message — never use generic boilerplate.
2. Use the dispute category and outcome guidance below; do not contradict them.
3. For payment disputes (PAYMENT_NOT_REFLECTED / PAYMENT_ALREADY_DONE), NEVER tell the customer
   that "invoice details are correct" when outcome is COMPANY_CORRECT — instead explain payment
   could not be verified and the balance remains outstanding.
4. Be concise: greeting, 2–4 short paragraphs, professional closing. No bullet lists unless
   requesting specific documents in NEED_MORE_INFO.
5. Subject must reference invoice {invoice_number or "number"} when available.
6. Sign off as "Paisa Vasool Support Team".
7. Do not mention internal systems, AI, agents, or associate approval steps.

DISPUTE CONTEXT
Category: {dispute_category}
Outcome: {outcome}
Customer email: {customer_email}
Invoice number: {invoice_number or "Not specified"}
Payment reference on file: {payment_reference or "None"}
Resolution reason (internal): {reason_line}

CUSTOMER'S MESSAGE (what they reported):
{customer_msg}

OUTCOME INSTRUCTIONS:
{outcome_guidance}

SUPPORTING CONTEXT
Activities:
{activities_summary or "None"}

Internal comments:
{comments_summary or "None"}

Invoice snapshot:
{invoice_summary or "Not available"}

Information to request from customer (if NEED_MORE_INFO):
{info_request or "N/A"}

Output a raw JSON object ONLY (no markdown fences):
{{
  "recipient": "string",
  "subject": "string",
  "body": "string"
}}
"""

    @classmethod
    def _template_fallback(
        cls,
        dispute_category: str,
        outcome: str,
        customer_email: str,
        info_request: str | None = None,
        invoice_number: str | None = None,
        payment_reference: str | None = None,
    ) -> dict[str, Any]:
        """Provides structured fallback templates when LLM is unavailable."""
        logger.info("Executing template fallback email generator")

        recipient = customer_email
        inv = invoice_number or "your invoice"
        subject = f"Update on your dispute — {inv}"

        if cls._is_payment_category(dispute_category):
            if outcome == "CUSTOMER_CORRECT":
                ref_line = (
                    f" using reference {payment_reference}" if payment_reference else ""
                )
                body = (
                    f"Dear Customer,\n\n"
                    f"Thank you for contacting us about payment for invoice {inv}.\n\n"
                    f"We have completed our review and located your payment{ref_line}. "
                    f"The payment has been applied to this invoice and the balance has been "
                    f"updated in our records.\n\n"
                    f"If you need a payment confirmation, please reply to this email.\n\n"
                    f"Best regards,\n"
                    f"Paisa Vasool Support Team"
                )
            elif outcome == "COMPANY_CORRECT":
                body = (
                    f"Dear Customer,\n\n"
                    f"Thank you for raising your concern about payment for invoice {inv}.\n\n"
                    f"We reviewed our payment records and were unable to verify a settled "
                    f"payment matching the details provided. The outstanding balance on this "
                    f"invoice remains unchanged at this time.\n\n"
                    f"If the payment was made, please reply with the UTR or transaction "
                    f"reference, payment date, amount, and remitting bank account so we can "
                    f"investigate further.\n\n"
                    f"Best regards,\n"
                    f"Paisa Vasool Support Team"
                )
            elif outcome == "NEED_MORE_INFO":
                request_text = info_request or (
                    "Please share the UTR or transaction reference, payment date, amount paid, "
                    "and the bank account used for the transfer."
                )
                body = (
                    f"Dear Customer,\n\n"
                    f"Thank you for contacting us regarding invoice {inv}.\n\n"
                    f"To complete our payment verification, we need the following:\n"
                    f"{request_text}\n\n"
                    f"Please reply with these details along with invoice number {inv}.\n\n"
                    f"Best regards,\n"
                    f"Paisa Vasool Support Team"
                )
            else:
                body = (
                    f"Dear Customer,\n\n"
                    f"We are reviewing your payment concern for invoice {inv} and will update "
                    f"you shortly.\n\n"
                    f"Best regards,\n"
                    f"Paisa Vasool Support Team"
                )
            return {"recipient": recipient, "subject": subject, "body": body}

        if outcome == "CUSTOMER_CORRECT":
            body = (
                f"Dear Customer,\n\n"
                f"Thank you for raising your dispute regarding invoice {inv}.\n\n"
                f"We have completed our review and confirmed your concern was valid. The "
                f"necessary correction has been applied to your account.\n\n"
                f"Best regards,\n"
                f"Paisa Vasool Support Team"
            )
        elif outcome == "COMPANY_CORRECT":
            body = (
                f"Dear Customer,\n\n"
                f"Thank you for contacting us about invoice {inv}.\n\n"
                f"After reviewing our records, the invoice remains correct as issued and no "
                f"amendment is required at this time.\n\n"
                f"If you have additional supporting documents, please reply and we will "
                f"re-review.\n\n"
                f"Best regards,\n"
                f"Paisa Vasool Support Team"
            )
        elif outcome == "NEED_MORE_INFO":
            request_text = (
                info_request
                or "We require additional information to complete our investigation."
            )
            invoice_prompt = (
                f"Please reply with the requested details along with invoice number {inv}."
                if invoice_number
                else "Please reply with the requested details along with your invoice number."
            )
            body = (
                f"Dear Customer,\n\n"
                f"{request_text}\n\n"
                f"{invoice_prompt}\n\n"
                f"Best regards,\n"
                f"Paisa Vasool Support Team"
            )
        elif outcome == "OPERATIONAL_ESCALATION" or "WAITING_INTERNAL" in outcome:
            body = (
                f"Dear Customer,\n\n"
                f"Your dispute regarding invoice {inv} has been forwarded to our operations "
                f"team for further review. We will contact you once the review is complete.\n\n"
                f"Best regards,\n"
                f"Paisa Vasool Support Team"
            )
        else:
            body = (
                f"Dear Customer,\n\n"
                f"We are reviewing your dispute for invoice {inv} and will update you when a "
                f"decision is reached.\n\n"
                f"Best regards,\n"
                f"Paisa Vasool Support Team"
            )

        return {"recipient": recipient, "subject": subject, "body": body}

    @classmethod
    def _compose_associate_draft_prompt(
        cls,
        *,
        dispute_category: str,
        dispute_status: str,
        resolution_outcome: str | None,
        customer_email: str,
        activities_summary: str,
        comments_summary: str,
        invoice_summary: str,
        invoice_number: str | None,
        customer_message_summary: str | None,
        payment_reference: str | None,
        resolution_reason: str | None,
        prior_outbound_summary: str,
        associate_instructions: str | None,
    ) -> str:
        customer_msg = cls._truncate(customer_message_summary) or "Not available."
        category_guidance = ""
        if cls._is_payment_category(dispute_category):
            category_guidance = (
                "This is a payment dispute. Focus on payment verification, UTR/reference, "
                "and outstanding balance. Do not claim invoice line items are correct unless "
                "explicitly supported by context."
            )
        elif dispute_category == "AMENDMENT":
            category_guidance = (
                "This is a billing amendment dispute. Address the specific charge or line item "
                "the customer questioned."
            )

        return f"""You are assisting a Paisa Vasool finance associate to draft a customer email.

The associate will review, edit, and send this message. Draft a helpful, contextual reply —
not generic boilerplate. Do not announce an automatic system resolution unless the context
clearly supports it.

RULES:
1. Address the customer's actual concern from their message history.
2. Use dispute category, status, and prior emails to stay consistent.
3. {category_guidance or "Stay specific to the dispute context."}
4. If associate instructions are provided below, follow them while staying professional.
5. Be concise: greeting, 2–4 short paragraphs, sign as "Paisa Vasool Support Team".
6. Subject must reference invoice {invoice_number or "number"} when available.
7. Set recipient to {customer_email}.

DISPUTE CONTEXT
Category: {dispute_category}
Status: {dispute_status}
Resolution outcome (if any): {resolution_outcome or "In progress"}
Payment reference on file: {payment_reference or "None"}
Internal resolution reason: {resolution_reason or "Not specified"}

CUSTOMER MESSAGE HISTORY:
{customer_msg}

PRIOR OUTBOUND EMAILS (system or associate — do not repeat verbatim):
{prior_outbound_summary}

ACTIVITIES:
{activities_summary or "None"}

INTERNAL COMMENTS:
{comments_summary or "None"}

INVOICE SNAPSHOT:
{invoice_summary or "Not available"}

ASSOCIATE INSTRUCTIONS (what they want this email to accomplish):
{associate_instructions or "Draft an appropriate follow-up based on the dispute context."}

Output a raw JSON object ONLY (no markdown fences):
{{
  "recipient": "string",
  "subject": "string",
  "body": "string"
}}
"""

    @classmethod
    def _associate_draft_fallback(
        cls,
        *,
        customer_email: str,
        invoice_number: str | None,
        dispute_category: str,
        associate_instructions: str | None,
    ) -> dict[str, Any]:
        inv = invoice_number or "your invoice"
        subject = f"Re: Your dispute — {inv}"
        instruction_hint = (
            f"\n\n{associate_instructions.strip()}"
            if associate_instructions and associate_instructions.strip()
            else ""
        )
        body = (
            f"Dear Customer,\n\n"
            f"Thank you for your message regarding invoice {inv} "
            f"({dispute_category.replace('_', ' ').lower()}).\n\n"
            f"We have reviewed your concern and are working on the next steps. "
            f"We will update you as soon as we have additional information."
            f"{instruction_hint}\n\n"
            f"Best regards,\n"
            f"Paisa Vasool Support Team"
        )
        return {"recipient": customer_email, "subject": subject, "body": body}

    @classmethod
    async def generate_associate_draft(
        cls,
        *,
        dispute_category: str,
        dispute_status: str,
        resolution_outcome: str | None,
        customer_email: str,
        activities_summary: str,
        comments_summary: str,
        invoice_summary: str,
        invoice_number: str | None = None,
        customer_message_summary: str | None = None,
        payment_reference: str | None = None,
        resolution_reason: str | None = None,
        prior_outbound_summary: str = "None",
        associate_instructions: str | None = None,
    ) -> dict[str, Any]:
        """Generates an associate-editable customer email draft using dispute context."""
        prompt = cls._compose_associate_draft_prompt(
            dispute_category=dispute_category,
            dispute_status=dispute_status,
            resolution_outcome=resolution_outcome,
            customer_email=customer_email,
            activities_summary=activities_summary,
            comments_summary=comments_summary,
            invoice_summary=invoice_summary,
            invoice_number=invoice_number,
            customer_message_summary=customer_message_summary,
            payment_reference=payment_reference,
            resolution_reason=resolution_reason,
            prior_outbound_summary=prior_outbound_summary,
            associate_instructions=associate_instructions,
        )
        default_subject = (
            f"Re: Invoice {invoice_number}" if invoice_number else "Re: Your dispute"
        )
        input_payload = {
            "mode": "associate_draft",
            "category": dispute_category,
            "status": dispute_status,
            "email": customer_email,
            "associate_instructions": associate_instructions,
        }
        fallback_res = cls._associate_draft_fallback(
            customer_email=customer_email,
            invoice_number=invoice_number,
            dispute_category=dispute_category,
            associate_instructions=associate_instructions,
        )
        return await cls._generate_json_from_prompt(
            prompt=prompt,
            customer_email=customer_email,
            default_subject=default_subject,
            input_payload=input_payload,
            fallback_res=fallback_res,
        )

    @classmethod
    async def _generate_json_from_prompt(
        cls,
        *,
        prompt: str,
        customer_email: str,
        default_subject: str,
        input_payload: dict[str, Any],
        fallback_res: dict[str, str],
    ) -> dict[str, Any]:
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
                    parsed = cls._clean_and_parse_json(content)
                    return {
                        "recipient": parsed.get("recipient", customer_email),
                        "subject": parsed.get("subject", default_subject),
                        "body": parsed.get("body", ""),
                        "agent_run_details": {
                            "prompt": prompt,
                            "input_payload": input_payload,
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
                parsed = cls._clean_and_parse_json(content)
                return {
                    "recipient": parsed.get("recipient", customer_email),
                    "subject": parsed.get("subject", default_subject),
                    "body": parsed.get("body", ""),
                    "agent_run_details": {
                        "prompt": prompt,
                        "input_payload": input_payload,
                        "output_payload": parsed,
                        "latency": latency,
                        "model": model,
                        "provider": provider,
                        "status": "SUCCESS",
                    },
                }
            except Exception as e:
                logger.error("Direct Gemini mail generation failed: %s", str(e))

        start_time = time.time()
        latency = time.time() - start_time
        return {
            "recipient": fallback_res["recipient"],
            "subject": fallback_res["subject"],
            "body": fallback_res["body"],
            "agent_run_details": {
                "prompt": prompt,
                "input_payload": input_payload,
                "output_payload": fallback_res,
                "latency": latency,
                "model": "template_fallback",
                "provider": "system",
                "status": "FALLBACK",
            },
        }

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
        customer_message_summary: str | None = None,
        payment_reference: str | None = None,
        resolution_reason: str | None = None,
    ) -> dict[str, Any]:
        """Generates customized email subject and body for dispute updates using LLM or fallback."""
        prompt = cls._compose_prompt(
            dispute_category=dispute_category,
            outcome=outcome,
            customer_email=customer_email,
            activities_summary=activities_summary,
            comments_summary=comments_summary,
            invoice_summary=invoice_summary,
            info_request=info_request,
            invoice_number=invoice_number,
            customer_message_summary=customer_message_summary,
            payment_reference=payment_reference,
            resolution_reason=resolution_reason,
        )
        fallback_res = cls._template_fallback(
            dispute_category,
            outcome,
            customer_email,
            info_request=info_request,
            invoice_number=invoice_number,
            payment_reference=payment_reference,
        )
        return await cls._generate_json_from_prompt(
            prompt=prompt,
            customer_email=customer_email,
            default_subject=f"Update on your dispute — {invoice_number or dispute_category}",
            input_payload={
                "category": dispute_category,
                "outcome": outcome,
                "email": customer_email,
                "payment_reference": payment_reference,
                "resolution_reason": resolution_reason,
            },
            fallback_res=fallback_res,
        )

    @classmethod
    def _clean_and_parse_json(cls, content: str) -> dict[str, Any]:
        """Cleans potential markdown blocks and parses JSON string."""
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\n", "", cleaned)
            cleaned = re.sub(r"\n```$", "", cleaned)
        return json.loads(cleaned.strip())
