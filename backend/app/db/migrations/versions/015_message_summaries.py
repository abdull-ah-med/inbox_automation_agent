"""Add structured Haiku summary columns to messages."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "015_message_summaries"
down_revision: str | None = "014_message_body_clean"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("summary_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("messages", sa.Column("summary_one_line", sa.Text(), nullable=True))
    op.add_column(
        "messages",
        sa.Column("summarized_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("messages", sa.Column("summary_model", sa.String(length=64), nullable=True))
    op.add_column(
        "messages",
        sa.Column("summary_clean_version", sa.SmallInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("messages", "summary_clean_version")
    op.drop_column("messages", "summary_model")
    op.drop_column("messages", "summarized_at")
    op.drop_column("messages", "summary_one_line")
    op.drop_column("messages", "summary_json")
