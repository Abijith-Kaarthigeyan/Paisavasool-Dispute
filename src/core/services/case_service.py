from datetime import datetime
from uuid import UUID

from src.core.exceptions.business_exceptions import CaseNotFoundException
from src.data.models.postgres.case import DisputeCase
from src.data.models.postgres.dispute import Dispute
from src.data.repositories.case_repository import CaseRepository
from src.data.repositories.dispute_repository import DisputeRepository
from src.schemas.auth import RoleName


class CaseService:
    def __init__(
        self,
        case_repo: CaseRepository,
        dispute_repo: DisputeRepository,
    ):
        self.case_repo = case_repo
        self.dispute_repo = dispute_repo

    async def list_cases(
        self,
        *,
        status: str | None = None,
        search: str | None = None,
        created_at_from: datetime | None = None,
        created_at_to: datetime | None = None,
        sort_by: str | None = None,
        sort_order: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[DisputeCase], int]:
        return await self.case_repo.list_cases(
            status=status,
            search=search,
            created_at_from=created_at_from,
            created_at_to=created_at_to,
            sort_by=sort_by,
            sort_order=sort_order,
            limit=limit,
            offset=offset,
        )

    async def get_case(self, case_id: UUID) -> DisputeCase:
        case = await self.case_repo.get_by_id(case_id)
        if not case:
            raise CaseNotFoundException(f"Case {case_id} not found.")
        return case

    async def list_disputes_for_case(self, case_id: UUID) -> list[Dispute]:
        return await self.dispute_repo.list_disputes_by_case_id(case_id)

    async def list_disputes_for_case_for_user(
        self, case_id: UUID, role: RoleName, user_id: UUID
    ) -> list[Dispute]:
        disputes = await self.list_disputes_for_case(case_id)
        if role == RoleName.FINANCE_ASSOCIATE:
            return [d for d in disputes if d.assigned_to == user_id]
        return disputes
