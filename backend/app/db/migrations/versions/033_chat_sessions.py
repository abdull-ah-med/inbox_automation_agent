"""Alembic: InboxAssistant conversation sessions for resume across refresh."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "033_chat_sessions"
down_revision: str | None = "032_chat_response_cache"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_TABLE = "chat_sessions"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE in set(inspector.get_table_names()):
        return
    op.create_table(
        _TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mailbox", sa.Text(), nullable=True),
        sa.Column(
            "messages",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_sessions_user_id", _TABLE, ["user_id"])
    op.create_index(
        "ix_chat_sessions_user_last_message",
        _TABLE,
        ["user_id", "last_message_at"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        return
    op.drop_index("ix_chat_sessions_user_last_message", table_name=_TABLE)
    op.drop_index("ix_chat_sessions_user_id", table_name=_TABLE)
    op.drop_table(_TABLE)
