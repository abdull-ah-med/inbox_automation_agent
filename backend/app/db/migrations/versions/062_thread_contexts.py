"""Add thread_contexts pointer and ADD-only thread_context_facts.

Per-thread working memory (Plan 3): Elise pins plus provenance-linked facts.
Supersession is a flag, never DELETE.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "062_thread_contexts"
down_revision: str | None = "061_teaching_notes_scope_index"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_CONTEXTS = "thread_contexts"
_FACTS = "thread_context_facts"
_FACTS_IX = "ix_thread_context_facts_thread_active_created"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if _CONTEXTS not in tables:
        op.create_table(
            _CONTEXTS,
            sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("user_notes", sa.Text(), nullable=False, server_default=""),
            sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column(
                "extract_input_hash",
                sa.String(64),
                nullable=False,
                server_default="",
            ),
            sa.Column(
                "last_message_id_at_extract",
                postgresql.UUID(as_uuid=True),
                nullable=True,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["thread_id"],
                ["threads.id"],
                name="fk_thread_contexts_thread",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["last_message_id_at_extract"],
                ["messages.id"],
                name="fk_thread_contexts_last_message",
                ondelete="SET NULL",
            ),
            sa.PrimaryKeyConstraint("thread_id"),
        )

    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if _FACTS not in tables:
        op.create_table(
            _FACTS,
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("source_message_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("actor_kind", sa.String(16), nullable=False),
            sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("superseded_by", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["thread_id"],
                ["threads.id"],
                name="fk_thread_context_facts_thread",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["source_message_id"],
                ["messages.id"],
                name="fk_thread_context_facts_source_message",
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["superseded_by"],
                [f"{_FACTS}.id"],
                name="fk_thread_context_facts_superseded_by",
                ondelete="SET NULL",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            _FACTS_IX,
            _FACTS,
            ["thread_id", "created_at"],
            unique=False,
            postgresql_where=sa.text("superseded_at IS NULL"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if _FACTS in tables:
        op.drop_index(_FACTS_IX, table_name=_FACTS)
        op.drop_table(_FACTS)
    if _CONTEXTS in tables:
        op.drop_table(_CONTEXTS)
