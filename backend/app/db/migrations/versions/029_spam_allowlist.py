"""Add spam_allowlist and persist Graph folder so Junk is a triage hint, not a verdict."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "029_spam_allowlist"
down_revision: str | None = "028_fts_fold_apostrophes"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    columns = {col["name"] for col in inspector.get_columns("messages")} if "messages" in tables else set()

    if "graph_folder" not in columns:
        op.add_column(
            "messages",
            sa.Column("graph_folder", sa.String(length=32), nullable=True),
        )

    if "spam_allowlist" in tables:
        return

    op.create_table(
        "spam_allowlist",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mailbox", sa.String(length=320), nullable=False),
        sa.Column("sender_address", sa.String(length=320), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor", sa.String(length=320), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["thread_id"], ["threads.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "mailbox",
            "sender_address",
            name="uq_spam_allowlist_mailbox_sender",
        ),
    )
    op.create_index("ix_spam_allowlist_mailbox", "spam_allowlist", ["mailbox"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    columns = {col["name"] for col in inspector.get_columns("messages")} if "messages" in tables else set()

    if "spam_allowlist" in tables:
        op.drop_index("ix_spam_allowlist_mailbox", table_name="spam_allowlist")
        op.drop_table("spam_allowlist")
    if "graph_folder" in columns:
        op.drop_column("messages", "graph_folder")
