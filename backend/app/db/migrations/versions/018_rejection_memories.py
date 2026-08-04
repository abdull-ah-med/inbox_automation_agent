"""Add rejection_memories table for negative draft constraints."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "018_rejection_memories"
down_revision: str | None = "017_routing_taxonomy"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_HNSW_INDEX = "ix_rejection_memories_embedding_hnsw"


def upgrade() -> None:
    op.create_table(
        "rejection_memories",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("draft_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mailbox", sa.String(length=255), nullable=False),
        sa.Column("routing_category", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=32), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=False),
        sa.Column(
            "is_excluded",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["draft_id"], ["drafts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("draft_id", name="uq_rejection_memories_draft_id"),
    )
    op.create_index("ix_rejection_memories_mailbox", "rejection_memories", ["mailbox"])
    op.create_index(
        "ix_rejection_memories_mailbox_category",
        "rejection_memories",
        ["mailbox", "routing_category"],
    )

    with op.get_context().autocommit_block():
        op.create_index(
            _HNSW_INDEX,
            "rejection_memories",
            ["embedding"],
            unique=False,
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            _HNSW_INDEX,
            table_name="rejection_memories",
            postgresql_concurrently=True,
        )
    op.drop_index("ix_rejection_memories_mailbox_category", table_name="rejection_memories")
    op.drop_index("ix_rejection_memories_mailbox", table_name="rejection_memories")
    op.drop_table("rejection_memories")
