"""remove_communication_attachments

Reverts the removed communication-attachments migration by dropping
dispute_communication_attachments if it still exists.

Revision ID: 000000000006
Revises: 000000000005
Create Date: 2026-06-28 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "000000000006"
down_revision: str | Sequence[str] | None = "000000000005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "DROP TABLE IF EXISTS dispute.dispute_communication_attachments CASCADE"
        )
    )


def downgrade() -> None:
    # Intentionally irreversible: communication attachments are not supported.
    pass
