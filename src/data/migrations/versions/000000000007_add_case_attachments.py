"""add_case_attachments

Revision ID: 000000000007
Revises: 000000000006
Create Date: 2026-06-29 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "000000000007"
down_revision: str | Sequence[str] | None = "000000000006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "case_attachments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("file_path", sa.String(length=512), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["case_id"], ["dispute.dispute_cases.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema="dispute",
    )
    op.create_index(
        "ix_case_attachments_case_id",
        "case_attachments",
        ["case_id"],
        schema="dispute",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_case_attachments_case_id",
        table_name="case_attachments",
        schema="dispute",
    )
    op.drop_table("case_attachments", schema="dispute")
