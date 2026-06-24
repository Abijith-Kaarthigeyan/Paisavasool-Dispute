"""alter_recommended_action_to_text

Revision ID: 000000000003
Revises: 000000000002
Create Date: 2026-06-24 15:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "000000000003"
down_revision: str | Sequence[str] | None = "000000000002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "dispute_resolution_recommendations",
        "recommended_action",
        type_=sa.Text(),
        existing_type=sa.String(length=255),
        schema="dispute",
    )


def downgrade() -> None:
    op.alter_column(
        "dispute_resolution_recommendations",
        "recommended_action",
        type_=sa.String(length=255),
        existing_type=sa.Text(),
        schema="dispute",
    )
