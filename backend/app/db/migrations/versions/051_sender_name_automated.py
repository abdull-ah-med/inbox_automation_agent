"""Persist Graph From display name and ingest-time automated flag.

Revision id shortened: alembic_version.version_num is VARCHAR(32).
ADD COLUMN IF NOT EXISTS: a prior 051 attempt added the columns then failed
when writing the 35-char revision id.

Revision ID: 051_sender_name_automated
Revises: 050_drop_redundant_indexes
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "051_sender_name_automated"
down_revision: str | None = "050_drop_redundant_indexes"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS sender_name TEXT"))
    op.execute(
        sa.text(
            "ALTER TABLE messages ADD COLUMN IF NOT EXISTS is_automated "
            "BOOLEAN NOT NULL DEFAULT false"
        )
    )


def downgrade() -> None:
    op.drop_column("messages", "is_automated")
    op.drop_column("messages", "sender_name")
