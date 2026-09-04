"""Add preference_pairs table and retrieved_atom/note_ids columns to drafts.

Phase 0 of Feedback Loops v2: stores paired approve/reject decisions per draft
and wires up draft-time retrieval columns.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "054_preference_pairs"
down_revision: str | None = "053_no_action_to_resolved"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_TABLE = "preference_pairs"
_HNSW = "ix_preference_pairs_embedding_hnsw"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if _TABLE not in tables:
        op.create_table(
            _TABLE,
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("draft_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("mailbox", sa.String(255), nullable=False),
            sa.Column("sender_address", sa.String(320), nullable=False),
            sa.Column("sender_domain", sa.String(255), nullable=False),
            sa.Column("routing_category", sa.String(32), nullable=False),
            sa.Column("email_text_hash", sa.String(64), nullable=False),
            sa.Column("email_embedding", Vector(1536), nullable=False),
            sa.Column("chosen_body", sa.Text(), nullable=True),
            sa.Column("rejected_body", sa.Text(), nullable=True),
            sa.Column("decision", sa.String(16), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["draft_id"], ["drafts.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["thread_id"], ["threads.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("draft_id", name="uq_preference_pairs_draft_id"),
        )
        op.create_index(
            "ix_preference_pairs_mailbox_sender",
            _TABLE,
            ["mailbox", "sender_address"],
        )
        op.create_index(
            "ix_preference_pairs_mailbox_category",
            _TABLE,
            ["mailbox", "routing_category"],
        )

    inspector = sa.inspect(bind)
    existing = {i["name"] for i in inspector.get_indexes(_TABLE)}
    if _HNSW not in existing:
        with op.get_context().autocommit_block():
            op.create_index(
                _HNSW,
                _TABLE,
                ["email_embedding"],
                unique=False,
                postgresql_using="hnsw",
                postgresql_ops={"email_embedding": "vector_cosine_ops"},
                postgresql_concurrently=True,
            )

    # Add retrieved_atom_ids and retrieved_note_ids to drafts
    cols = {c["name"] for c in inspector.get_columns("drafts")}
    if "retrieved_atom_ids" not in cols:
        op.add_column(
            "drafts",
            sa.Column(
                "retrieved_atom_ids",
                postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
                nullable=True,
            ),
        )
    if "retrieved_note_ids" not in cols:
        op.add_column(
            "drafts",
            sa.Column(
                "retrieved_note_ids",
                postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
                nullable=True,
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    cols = {c["name"] for c in inspector.get_columns("drafts")}
    if "retrieved_note_ids" in cols:
        op.drop_column("drafts", "retrieved_note_ids")
    if "retrieved_atom_ids" in cols:
        op.drop_column("drafts", "retrieved_atom_ids")

    if _TABLE not in tables:
        return
    existing = {i["name"] for i in inspector.get_indexes(_TABLE)}
    if _HNSW in existing:
        with op.get_context().autocommit_block():
            op.drop_index(_HNSW, table_name=_TABLE, postgresql_concurrently=True)
    op.drop_index("ix_preference_pairs_mailbox_category", table_name=_TABLE)
    op.drop_index("ix_preference_pairs_mailbox_sender", table_name=_TABLE)
    op.drop_table(_TABLE)
