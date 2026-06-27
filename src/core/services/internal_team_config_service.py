from uuid import UUID

from src.core.exceptions.business_exceptions import ValidationException
from src.data.models.postgres.internal_team_contact import InternalTeamContact
from src.data.repositories.internal_team_contact_repository import (
    InternalTeamContactRepository,
)

INTERNAL_TEAM_KEYS = frozenset({"FINANCE_TEAM", "QUALITY_TEAM", "LOGISTICS_TEAM"})

DEFAULT_TEAM_EMAILS = {
    "FINANCE_TEAM": "finance_team@paisavasool.com",
    "QUALITY_TEAM": "quality_team@paisavasool.com",
    "LOGISTICS_TEAM": "logistics_team@paisavasool.com",
}


class InternalTeamConfigService:
    """Resolves internal escalation recipients and manages admin configuration."""

    def __init__(self, repository: InternalTeamContactRepository):
        self.repository = repository

    @staticmethod
    def resolve_team_key(dispute_category: str) -> str:
        if dispute_category == "QUALITY":
            return "QUALITY_TEAM"
        if dispute_category == "LATE_DELIVERY":
            return "LOGISTICS_TEAM"
        return "FINANCE_TEAM"

    async def get_email_for_category(self, dispute_category: str) -> str:
        team_key = self.resolve_team_key(dispute_category)
        return await self.get_email_for_team_key(team_key)

    async def get_email_for_team_key(self, team_key: str) -> str:
        contact = await self.repository.get_by_team_key(team_key)
        if contact:
            return contact.email
        return DEFAULT_TEAM_EMAILS.get(team_key, DEFAULT_TEAM_EMAILS["FINANCE_TEAM"])

    async def list_contacts(self) -> list[InternalTeamContact]:
        return await self.repository.list_all()

    async def update_contacts(
        self,
        updates: list[tuple[str, str]],
        *,
        updated_by: UUID | None = None,
    ) -> list[InternalTeamContact]:
        if not updates:
            raise ValidationException("At least one team contact must be provided.")

        updated: list[InternalTeamContact] = []
        for team_key, email in updates:
            if team_key not in INTERNAL_TEAM_KEYS:
                raise ValidationException(f"Unknown internal team key: {team_key}")
            contact = await self.repository.update_email(
                team_key,
                email=email,
                updated_by=updated_by,
            )
            if not contact:
                raise ValidationException(
                    f"Internal team contact not found: {team_key}"
                )
            updated.append(contact)
        return updated
