"""Add body_clean + content_type columns to messages.

Nullable clean columns + a defaulted content_type — no long locks on PG 11+.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "014_message_body_clean"
down_revision: str | None = "013_drop_bool_indexes"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column(
            "body_content_type",
            sa.String(length=16),
            nullable=False,
            server_default="text",
        ),
    )
    op.add_column("messages", sa.Column("body_clean", sa.Text(), nullable=True))
    op.add_column(
        "messages",
        sa.Column("body_clean_version", sa.SmallInteger(), nullable=True),
    )
    op.add_column(
        "messages",
        sa.Column("body_clean_computed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("messages", "body_clean_computed_at")
    op.drop_column("messages", "body_clean_version")
    op.drop_column("messages", "body_clean")
    op.drop_column("messages", "body_content_type")
