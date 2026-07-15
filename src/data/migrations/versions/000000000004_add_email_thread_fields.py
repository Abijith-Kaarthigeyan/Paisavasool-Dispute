"""add_email_thread_fields

Revision ID: 000000000004
Revises: 000000000003
Create Date: 2026-06-26 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "000000000004"
down_revision: str | Sequence[str] | None = "000000000003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dispute_cases",
        sa.Column("gmail_thread_id", sa.String(length=255), nullable=True),
        schema="dispute",
    )
    op.add_column(
        "dispute_cases",
        sa.Column("rfc_message_id", sa.String(length=512), nullable=True),
        schema="dispute",
    )
    op.create_index(
        "ix_dispute_cases_gmail_thread_id",
        "dispute_cases",
        ["gmail_thread_id"],
        unique=False,
        schema="dispute",
    )
    op.add_column(
        "dispute_communications",
        sa.Column("rfc_message_id", sa.String(length=512), nullable=True),
        schema="dispute",
    )
    op.add_column(
        "dispute_communications",
        sa.Column("gmail_message_id", sa.String(length=255), nullable=True),
        schema="dispute",
    )


def downgrade() -> None:
    op.drop_column("dispute_communications", "gmail_message_id", schema="dispute")
    op.drop_column("dispute_communications", "rfc_message_id", schema="dispute")
    op.drop_index(
        "ix_dispute_cases_gmail_thread_id",
        table_name="dispute_cases",
        schema="dispute",
    )
    op.drop_column("dispute_cases", "rfc_message_id", schema="dispute")
    op.drop_column("dispute_cases", "gmail_thread_id", schema="dispute")
