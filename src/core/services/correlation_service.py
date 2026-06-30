import re
from uuid import UUID

from src.core.services.audit_service import AuditService
from src.core.services.email_thread_utils import extract_reference_tokens
from src.core.workflow.triage_agent import DisputeTriageAgent
from src.data.repositories.case_repository import CaseRepository
from src.data.repositories.communication_repository import CommunicationRepository
from src.data.repositories.dispute_repository import DisputeRepository
from src.data.repositories.other_repositories import CommentRepository


class CorrelationService:
    # Collapse granular types to AMENDMENT
    COLLAPSED_CATEGORIES = {
        "PRICING",
        "TAX",
        "QUANTITY",
        "WRONG_PRODUCT",
        "WRONG_CUSTOMER",
        "WRONG_INVOICE_DATE",
        "WRONG_DUE_DATE",
        "WRONG_TOTAL",
        "AMENDMENT",
    }

    PAYMENT_CATEGORIES = frozenset({"PAYMENT_ALREADY_DONE", "PAYMENT_NOT_REFLECTED"})

    ACTIVE_STATUSES = [
        "OPEN",
        "IN_REVIEW",
        "WAITING_CUSTOMER",
        "WAITING_INTERNAL_TEAM",
        "WAITING_ASSOCIATE_APPROVAL",
        "WAITING_PAYMENT_REVIEW",
    ]

    SYSTEM_USER_ID = UUID("00000000-0000-0000-0000-000000000001")
    DISPUTE_NUMBER_PATTERN = re.compile(r"\bDISP-\d{4}-\d{6}\b", re.IGNORECASE)

    def __init__(
        self,
        dispute_repo: DisputeRepository,
        communication_repo: CommunicationRepository,
        comment_repo: CommentRepository,
        audit_service: AuditService,
        case_repo: CaseRepository | None = None,
    ):
        self.dispute_repo = dispute_repo
        self.communication_repo = communication_repo
        self.comment_repo = comment_repo
        self.audit_service = audit_service
        self.case_repo = case_repo

    def collapse_category(self, category: str) -> str:
        """Collapses granular dispute categories into AMENDMENT or leaves them independent."""
        upper_cat = category.upper().strip()
        if upper_cat in self.COLLAPSED_CATEGORIES:
            return "AMENDMENT"
        return upper_cat

    def categories_compatible(
        self, existing_category: str, incoming_category: str
    ) -> bool:
        """Checks whether an incoming dispute category can attach to an existing dispute."""
        existing = self.collapse_category(existing_category)
        incoming = self.collapse_category(incoming_category)
        if existing == incoming:
            return True
        return (
            existing in self.PAYMENT_CATEGORIES and incoming in self.PAYMENT_CATEGORIES
        )

    def _infer_categories_from_content(self, content: str) -> list[str]:
        """Infers dispute categories from email text using triage keyword rules."""
        triage_result = DisputeTriageAgent._regex_fallback("", "", content)
        categories: list[str] = []
        for inv in triage_result.get("invoices", []):
            for category in inv.get("dispute_types") or []:
                normalized = DisputeTriageAgent.normalize_category(category)
                if normalized not in categories:
                    categories.append(normalized)
        return categories or ["OTHER"]

    def _rank_categories_for_content(
        self, categories: list[str], content: str
    ) -> list[str]:
        """Orders categories so signals in the email prefer the most relevant dispute type."""
        if not categories:
            return ["OTHER"]

        content_lower = (content or "").lower()
        payment_signals = any(
            kw in content_lower
            for kw in (
                "utr",
                "payment",
                "not reflected",
                "bank transfer",
                "neft",
                "imps",
                "rtgs",
                "transaction",
                "paid",
            )
        )
        amendment_signals = any(
            kw in content_lower
            for kw in (
                "tax",
                "pricing",
                "price",
                "quantity",
                "wrong product",
                "amendment",
                "incorrect charge",
            )
        )

        unique_categories = list(dict.fromkeys(categories))

        def sort_key(category: str) -> int:
            collapsed = self.collapse_category(category)
            if payment_signals and not amendment_signals:
                if collapsed in self.PAYMENT_CATEGORIES:
                    return 0
                if collapsed == "AMENDMENT":
                    return 2
            if amendment_signals and not payment_signals:
                if collapsed == "AMENDMENT":
                    return 0
                if collapsed in self.PAYMENT_CATEGORIES:
                    return 2
            return 1

        return sorted(unique_categories, key=sort_key)

    def _select_best_match(
        self,
        candidates: list,
        raw_category: str,
    ):
        """Picks the best dispute using category fit, preferring WAITING_CUSTOMER when tied."""
        if not candidates:
            return None

        collapsed_incoming = self.collapse_category(raw_category)

        def is_compatible(dispute) -> bool:
            return self.categories_compatible(
                dispute.dispute_category, collapsed_incoming
            )

        waiting_compatible = [
            dispute
            for dispute in candidates
            if dispute.status == "WAITING_CUSTOMER" and is_compatible(dispute)
        ]
        if waiting_compatible:
            return waiting_compatible[0]

        compatible = [dispute for dispute in candidates if is_compatible(dispute)]
        if compatible:
            waiting = [
                dispute
                for dispute in compatible
                if dispute.status == "WAITING_CUSTOMER"
            ]
            return waiting[0] if waiting else compatible[0]

        if collapsed_incoming == "OTHER":
            waiting_customer = [
                dispute
                for dispute in candidates
                if dispute.status == "WAITING_CUSTOMER"
            ]
            if len(waiting_customer) == 1:
                return waiting_customer[0]

        return None

    def _extract_dispute_number(self, content: str) -> str | None:
        match = self.DISPUTE_NUMBER_PATTERN.search(content or "")
        if not match:
            return None
        return match.group(0).upper()

    def _extract_invoice_numbers_from_text(self, content: str) -> list[str]:
        """Extract invoice numbers from full email content, including quoted replies."""
        triage_result = DisputeTriageAgent._regex_fallback("", "", content)
        return [
            inv["invoice_number"]
            for inv in triage_result.get("invoices", [])
            if inv.get("invoice_number")
        ]

    async def _reopen_if_waiting_customer(self, dispute) -> None:
        if dispute and dispute.status == "WAITING_CUSTOMER":
            dispute.status = "OPEN"
            await self.dispute_repo.update_dispute(dispute)

    async def _attach_inbound_communication(
        self,
        *,
        matched_dispute,
        customer_email: str,
        email_subject: str,
        email_body: str,
        raw_content: str | None = None,
    ) -> None:
        message_content = (raw_content or email_body or "").strip()
        comm = await self.communication_repo.create_communication(
            dispute_id=matched_dispute.id,
            recipient=customer_email,
            subject=email_subject,
            body=message_content or email_body,
            communication_type="CUSTOMER",
        )

        created_by = matched_dispute.assigned_to or self.SYSTEM_USER_ID

        if message_content:
            await self.comment_repo.create_comment(
                dispute_id=matched_dispute.id,
                comment=message_content,
                comment_type="CUSTOMER_COMMENT",
                created_by=created_by,
            )

        comment_text = f"Correlated & attached communication: '{email_subject}' from {customer_email}."
        await self.comment_repo.create_comment(
            dispute_id=matched_dispute.id,
            comment=comment_text,
            comment_type="CUSTOMER",
            created_by=created_by,
        )

        await self.audit_service.log_event(
            dispute_id=matched_dispute.id,
            action="COMMENT_ADDED",
            performed_by=created_by,
            metadata={"comment_id": str(comm.id), "source": "correlation"},
        )

        from src.infrastructure.celery.tasks import generate_associate_draft_task

        generate_associate_draft_task.apply_async(
            args=[str(matched_dispute.id), str(comm.id)],
            countdown=3,
        )

    def _active_disputes_for_case(self, case, customer_email: str) -> list:
        normalized_email = (customer_email or "").strip().lower()
        case_email = (case.customer_email or "").strip().lower()
        if normalized_email and case_email and normalized_email != case_email:
            return []

        active = [
            dispute
            for dispute in case.disputes or []
            if not dispute.is_deleted and dispute.status in self.ACTIVE_STATUSES
        ]
        active.sort(key=lambda dispute: dispute.updated_at, reverse=True)
        return active

    def _pick_dispute_from_cases(
        self,
        cases: list,
        inferred_categories: list[str],
        customer_email: str,
    ):
        candidates: list = []
        for case in cases:
            candidates.extend(self._active_disputes_for_case(case, customer_email))
        if not candidates:
            return None

        for category in inferred_categories:
            matched = self._select_best_match(candidates, category)
            if matched:
                return matched

        waiting = [
            dispute for dispute in candidates if dispute.status == "WAITING_CUSTOMER"
        ]
        if len(waiting) == 1:
            return waiting[0]
        if len(candidates) == 1:
            return candidates[0]
        return None

    async def _find_dispute_by_communication_tokens(self, tokens: list[str]):
        if not tokens:
            return None

        for token in tokens:
            communications = await self.communication_repo.find_by_message_token(token)
            for communication in communications:
                dispute = await self.dispute_repo.get_by_id(communication.dispute_id)
                if dispute and dispute.status in self.ACTIVE_STATUSES:
                    return dispute
        return None

    async def _correlate_by_email_thread(
        self,
        *,
        gmail_thread_id: str | None,
        in_reply_to: str | None,
        email_references: str | None,
        customer_email: str,
        email_subject: str,
        email_body: str,
        raw_content: str | None,
        inferred_categories: list[str],
    ) -> UUID | None:
        if not self.case_repo:
            return None

        if gmail_thread_id:
            cases = await self.case_repo.find_by_gmail_thread_id(gmail_thread_id)
            matched_dispute = self._pick_dispute_from_cases(
                cases, inferred_categories, customer_email
            )
            if matched_dispute:
                await self._attach_inbound_communication(
                    matched_dispute=matched_dispute,
                    customer_email=customer_email,
                    email_subject=email_subject,
                    email_body=email_body,
                    raw_content=raw_content,
                )
                await self._reopen_if_waiting_customer(matched_dispute)
                return matched_dispute.id

        reference_tokens = extract_reference_tokens(in_reply_to, email_references)
        for token in reference_tokens:
            case = await self.case_repo.find_by_message_token(token)
            if case:
                matched_dispute = self._pick_dispute_from_cases(
                    [case], inferred_categories, customer_email
                )
                if matched_dispute:
                    await self._attach_inbound_communication(
                        matched_dispute=matched_dispute,
                        customer_email=customer_email,
                        email_subject=email_subject,
                        email_body=email_body,
                        raw_content=raw_content,
                    )
                    await self._reopen_if_waiting_customer(matched_dispute)
                    return matched_dispute.id

        matched_dispute = await self._find_dispute_by_communication_tokens(
            reference_tokens
        )
        if matched_dispute:
            if customer_email and matched_dispute.case:
                case_email = (matched_dispute.case.customer_email or "").strip().lower()
                sender_email = customer_email.strip().lower()
                if case_email and sender_email != case_email:
                    return None
            await self._attach_inbound_communication(
                matched_dispute=matched_dispute,
                customer_email=customer_email,
                email_subject=email_subject,
                email_body=email_body,
                raw_content=raw_content,
            )
            await self._reopen_if_waiting_customer(matched_dispute)
            return matched_dispute.id

        return None

    async def correlate_dispute(
        self,
        *,
        invoice_number: str,
        raw_category: str,
        customer_email: str,
        email_subject: str,
        email_body: str,
        raw_content: str | None = None,
        exclude_dispute_id: UUID | None = None,
    ) -> UUID | None:
        """Correlates an incoming dispute detail against active dispute workflows.

        If a match is found, attaches the email as a communication/comment and returns the dispute ID.
        If no match is found, returns None (caller should create a new dispute workflow).
        """
        normalized_invoice = DisputeTriageAgent.normalize_invoice_number(invoice_number)
        if not normalized_invoice:
            return None

        candidates = await self.dispute_repo.find_active_disputes_by_invoice_number(
            invoice_number=normalized_invoice,
            active_statuses=self.ACTIVE_STATUSES,
            exclude_dispute_id=exclude_dispute_id,
        )
        matched_dispute = self._select_best_match(candidates, raw_category)
        if not matched_dispute:
            return None

        await self._attach_inbound_communication(
            matched_dispute=matched_dispute,
            customer_email=customer_email,
            email_subject=email_subject,
            email_body=email_body,
            raw_content=raw_content,
        )
        await self._reopen_if_waiting_customer(matched_dispute)

        return matched_dispute.id

    async def find_correlated_dispute_for_intake(
        self,
        *,
        invoices: list[dict],
        customer_email: str,
        email_subject: str,
        email_body: str,
        raw_content: str | None = None,
        gmail_thread_id: str | None = None,
        in_reply_to: str | None = None,
        email_references: str | None = None,
    ) -> UUID | None:
        """Correlates an incoming case email before new disputes are generated."""
        content = raw_content or f"{email_subject}\n{email_body}"
        inferred_categories = self._rank_categories_for_content(
            self._infer_categories_from_content(content),
            content,
        )

        thread_match = await self._correlate_by_email_thread(
            gmail_thread_id=gmail_thread_id,
            in_reply_to=in_reply_to,
            email_references=email_references,
            customer_email=customer_email,
            email_subject=email_subject,
            email_body=email_body,
            raw_content=raw_content,
            inferred_categories=inferred_categories,
        )
        if thread_match:
            return thread_match

        dispute_number = self._extract_dispute_number(content)
        if dispute_number:
            dispute = await self.dispute_repo.get_by_dispute_number(dispute_number)
            if dispute and dispute.status in self.ACTIVE_STATUSES:
                await self._attach_inbound_communication(
                    matched_dispute=dispute,
                    customer_email=customer_email,
                    email_subject=email_subject,
                    email_body=email_body,
                    raw_content=raw_content,
                )
                await self._reopen_if_waiting_customer(dispute)
                return dispute.id

        invoice_category_pairs: list[tuple[str, str]] = []
        for inv in invoices:
            inv_num = inv.get("invoice_number", "")
            for category in inv.get("dispute_types") or ["OTHER"]:
                invoice_category_pairs.append((inv_num, category))

        ranked_categories = self._rank_categories_for_content(
            [category for _, category in invoice_category_pairs],
            content,
        )
        invoice_category_pairs.sort(
            key=lambda pair: (
                ranked_categories.index(pair[1])
                if pair[1] in ranked_categories
                else len(ranked_categories)
            )
        )

        for inv_num, category in invoice_category_pairs:
            matched_id = await self.correlate_dispute(
                invoice_number=inv_num,
                raw_category=category,
                customer_email=customer_email,
                email_subject=email_subject,
                email_body=email_body,
                raw_content=raw_content,
            )
            if matched_id:
                return matched_id

        extracted_invoices = self._extract_invoice_numbers_from_text(content)
        for inv_num in extracted_invoices:
            for category in inferred_categories:
                matched_id = await self.correlate_dispute(
                    invoice_number=inv_num,
                    raw_category=category,
                    customer_email=customer_email,
                    email_subject=email_subject,
                    email_body=email_body,
                    raw_content=raw_content,
                )
                if matched_id:
                    return matched_id

        waiting_disputes = (
            await self.dispute_repo.find_waiting_customer_disputes_by_email(
                customer_email=customer_email,
            )
        )
        if waiting_disputes:
            for category in inferred_categories:
                for dispute in waiting_disputes:
                    if self.categories_compatible(dispute.dispute_category, category):
                        await self._attach_inbound_communication(
                            matched_dispute=dispute,
                            customer_email=customer_email,
                            email_subject=email_subject,
                            email_body=email_body,
                            raw_content=raw_content,
                        )
                        await self._reopen_if_waiting_customer(dispute)
                        return dispute.id
            if len(waiting_disputes) == 1:
                dispute = waiting_disputes[0]
                await self._attach_inbound_communication(
                    matched_dispute=dispute,
                    customer_email=customer_email,
                    email_subject=email_subject,
                    email_body=email_body,
                    raw_content=raw_content,
                )
                await self._reopen_if_waiting_customer(dispute)
                return dispute.id

        return None
