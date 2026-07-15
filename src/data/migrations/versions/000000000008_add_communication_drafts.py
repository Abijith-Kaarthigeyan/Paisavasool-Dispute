"""add_communication_drafts

Revision ID: 000000000008
Revises: 000000000007
Create Date: 2026-06-30 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "000000000008"
down_revision: str | Sequence[str] | None = "000000000007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dispute_communication_drafts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("dispute_id", sa.Uuid(), nullable=False),
        sa.Column("recipient", sa.String(length=255), nullable=True),
        sa.Column("subject", sa.String(length=255), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("source_communication_id", sa.Uuid(), nullable=True),
        sa.Column("trigger", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["dispute_id"], ["dispute.disputes.id"]),
        sa.ForeignKeyConstraint(
            ["source_communication_id"], ["dispute.dispute_communications.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="dispute",
    )
    op.create_index(
        "ix_dispute_communication_drafts_dispute_id",
        "dispute_communication_drafts",
        ["dispute_id"],
        schema="dispute",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_dispute_communication_drafts_dispute_id",
        table_name="dispute_communication_drafts",
        schema="dispute",
    )
    op.drop_table("dispute_communication_drafts", schema="dispute")
