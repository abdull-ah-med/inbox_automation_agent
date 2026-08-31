"""Add urgency_feedbacks table and threads.urgency_reason.

Manual urgency edits feed future draft urgency via cosine similarity RAG.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

# Must be <= 32 chars (alembic_version.version_num VARCHAR(32)).
revision: str = "025_urgency_feedback"
down_revision: str | None = "024_approval_learning"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_HNSW_INDEX = "ix_urgency_feedbacks_embedding_hnsw"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    thread_cols = {c["name"] for c in inspector.get_columns("threads")}
    if "urgency_reason" not in thread_cols:
        op.add_column("threads", sa.Column("urgency_reason", sa.Text(), nullable=True))

    tables = set(inspector.get_table_names())
    if "urgency_feedbacks" not in tables:
        op.create_table(
            "urgency_feedbacks",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("draft_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("mailbox", sa.String(length=255), nullable=False),
            sa.Column("routing_category", sa.String(length=32), nullable=True),
            sa.Column("previous_urgency", sa.String(length=16), nullable=True),
            sa.Column("new_urgency", sa.String(length=16), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("embedding", Vector(1536), nullable=False),
            sa.Column("edited_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
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
            sa.ForeignKeyConstraint(["thread_id"], ["threads.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_urgency_feedbacks_mailbox", "urgency_feedbacks", ["mailbox"])
        op.create_index(
            "ix_urgency_feedbacks_mailbox_category",
            "urgency_feedbacks",
            ["mailbox", "routing_category"],
        )
        op.create_index("ix_urgency_feedbacks_draft_id", "urgency_feedbacks", ["draft_id"])
        op.create_index("ix_urgency_feedbacks_thread_id", "urgency_feedbacks", ["thread_id"])

    inspector = sa.inspect(bind)
    existing = {i["name"] for i in inspector.get_indexes("urgency_feedbacks")}
    if _HNSW_INDEX not in existing:
        with op.get_context().autocommit_block():
            op.create_index(
                _HNSW_INDEX,
                "urgency_feedbacks",
                ["embedding"],
                unique=False,
                postgresql_using="hnsw",
                postgresql_ops={"embedding": "vector_cosine_ops"},
                postgresql_concurrently=True,
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "urgency_feedbacks" in tables:
        existing = {i["name"] for i in inspector.get_indexes("urgency_feedbacks")}
        if _HNSW_INDEX in existing:
            with op.get_context().autocommit_block():
                op.drop_index(
                    _HNSW_INDEX,
                    table_name="urgency_feedbacks",
                    postgresql_concurrently=True,
                )
        op.drop_table("urgency_feedbacks")

    thread_cols = {c["name"] for c in inspector.get_columns("threads")}
    if "urgency_reason" in thread_cols:
        op.drop_column("threads", "urgency_reason")
