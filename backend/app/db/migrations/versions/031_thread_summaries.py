"""Thread-level summaries for InboxAssistant get_thread on long conversations."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "031_thread_summaries"
down_revision: str | None = "030_hnsw_production"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "thread_summaries" in set(inspector.get_table_names()):
        return
    op.create_table(
        "thread_summaries",
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column("message_count", sa.Integer(), nullable=False),
        sa.Column("last_message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["last_message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["thread_id"], ["threads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("thread_id"),
    )
    op.create_index(
        "ix_thread_summaries_last_message_id",
        "thread_summaries",
        ["last_message_id"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "thread_summaries" not in set(inspector.get_table_names()):
        return
    op.drop_index("ix_thread_summaries_last_message_id", table_name="thread_summaries")
    op.drop_table("thread_summaries")
