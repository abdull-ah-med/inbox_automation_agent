"""Alert fingerprint on threads plus suppress-escalation feedback.

Revision ID: 041_alert_fingerprint
Revises: 040_chat_cache_retention
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "041_alert_fingerprint"
down_revision: str | None = "040_chat_cache_retention"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("threads", sa.Column("alert_fingerprint", sa.Text(), nullable=True))
    op.create_index(
        "ix_threads_mailbox_alert_fingerprint_last_message_at",
        "threads",
        ["mailbox", "alert_fingerprint", "last_message_at"],
        postgresql_where=sa.text("alert_fingerprint IS NOT NULL"),
    )
    op.create_table(
        "alert_fingerprint_feedbacks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("mailbox", sa.String(length=320), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("source_thread_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor", sa.String(length=320), nullable=False),
        sa.Column("previous_urgency", sa.String(length=16), nullable=True),
        sa.Column("assessed_urgency", sa.String(length=16), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["source_thread_id"], ["threads.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_alert_fingerprint_feedbacks_mailbox_fingerprint",
        "alert_fingerprint_feedbacks",
        ["mailbox", "fingerprint"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_alert_fingerprint_feedbacks_mailbox_fingerprint",
        table_name="alert_fingerprint_feedbacks",
    )
    op.drop_table("alert_fingerprint_feedbacks")
    op.drop_index(
        "ix_threads_mailbox_alert_fingerprint_last_message_at",
        table_name="threads",
    )
    op.drop_column("threads", "alert_fingerprint")
