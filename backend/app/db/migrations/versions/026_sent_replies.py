"""Add sent_replies table for outbound reply → RESOLVED detection."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# Must be <= 32 chars (alembic_version.version_num VARCHAR(32)).
revision: str = "026_sent_replies"
down_revision: str | None = "025_urgency_feedback"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "sent_replies" in tables:
        return

    op.create_table(
        "sent_replies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("draft_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sent_body_snapshot", sa.Text(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("matched_by", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["thread_id"], ["threads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["draft_id"], ["drafts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id", name="uq_sent_replies_message_id"),
    )
    op.create_index("ix_sent_replies_thread_id", "sent_replies", ["thread_id"])
    op.create_index("ix_sent_replies_draft_id", "sent_replies", ["draft_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "sent_replies" not in tables:
        return
    op.drop_index("ix_sent_replies_draft_id", table_name="sent_replies")
    op.drop_index("ix_sent_replies_thread_id", table_name="sent_replies")
    op.drop_table("sent_replies")
