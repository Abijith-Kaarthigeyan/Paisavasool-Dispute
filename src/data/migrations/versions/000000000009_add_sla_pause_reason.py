"""add_sla_pause_reason

Revision ID: 000000000009
Revises: 000000000008
Create Date: 2026-07-02 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "000000000009"
down_revision: str | Sequence[str] | None = "000000000008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dispute_sla",
        sa.Column("pause_reason", sa.String(length=50), nullable=True),
        schema="dispute",
    )
    op.add_column(
        "dispute_communications",
        sa.Column(
            "pause_sla_till_reply",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        schema="dispute",
    )


def downgrade() -> None:
    op.drop_column("dispute_communications", "pause_sla_till_reply", schema="dispute")
    op.drop_column("dispute_sla", "pause_reason", schema="dispute")
