"""Persist Graph From display name and ingest-time automated flag.

Revision ID: 051_message_sender_name_automated
Revises: 050_drop_redundant_indexes
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "051_message_sender_name_automated"
down_revision: str | None = "050_drop_redundant_indexes"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("sender_name", sa.Text(), nullable=True))
    op.add_column(
        "messages",
        sa.Column("is_automated", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("messages", "is_automated")
    op.drop_column("messages", "sender_name")
