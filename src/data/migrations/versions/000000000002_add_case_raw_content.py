"""add_case_raw_content

Revision ID: 000000000002
Revises: 000000000001
Create Date: 2026-06-24 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "000000000002"
down_revision: str | Sequence[str] | None = "000000000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dispute_cases",
        sa.Column("raw_content", sa.Text(), nullable=True),
        schema="dispute",
    )


def downgrade() -> None:
    op.drop_column("dispute_cases", "raw_content", schema="dispute")
