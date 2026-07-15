"""add_internal_team_contacts

Revision ID: 000000000005
Revises: 000000000004
Create Date: 2026-06-27 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "000000000005"
down_revision: str | Sequence[str] | None = "000000000004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_TEAMS = [
    ("FINANCE_TEAM", "Finance team", "finance_team@paisavasool.com"),
    ("QUALITY_TEAM", "Quality team", "quality_team@paisavasool.com"),
    ("LOGISTICS_TEAM", "Logistics team", "logistics_team@paisavasool.com"),
]


def upgrade() -> None:
    op.create_table(
        "internal_team_contacts",
        sa.Column("team_key", sa.String(length=50), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("team_key"),
        schema="dispute",
    )

    for team_key, display_name, email in DEFAULT_TEAMS:
        op.execute(
            sa.text(
                "INSERT INTO dispute.internal_team_contacts "
                "(team_key, display_name, email) "
                "VALUES (:team_key, :display_name, :email)"
            ).bindparams(
                team_key=team_key,
                display_name=display_name,
                email=email,
            )
        )


def downgrade() -> None:
    op.drop_table("internal_team_contacts", schema="dispute")
