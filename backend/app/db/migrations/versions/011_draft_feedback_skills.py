"""Add draft feedback fields, skills table, and reply_embeddings.

Extends drafts with feedback_note / feedback_action / suggested_actions.
Creates skills (editable instruction packs) and reply_embeddings (approved
reply memory for tone RAG). HNSW index on reply_embeddings uses cosine ops
to match cosine_distance queries (db-patterns.mdc).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

# Must stay ≤32 chars — alembic_version.version_num is VARCHAR(32).
revision: str = "011_draft_feedback_skills"
down_revision: str | None = "010_msg_has_attachments"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_REPLY_HNSW_INDEX = "ix_reply_embeddings_embedding_hnsw"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    draft_cols = {c["name"] for c in inspector.get_columns("drafts")}
    if "feedback_note" not in draft_cols:
        op.add_column(
            "drafts",
            sa.Column("feedback_note", sa.Text(), nullable=True),
        )
    if "feedback_action" not in draft_cols:
        op.add_column(
            "drafts",
            sa.Column("feedback_action", sa.String(length=16), nullable=True),
        )
    if "suggested_actions" not in draft_cols:
        op.add_column(
            "drafts",
            sa.Column(
                "suggested_actions",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
                server_default=sa.text("'[]'::jsonb"),
            ),
        )

    if "skills" not in tables:
        op.create_table(
            "skills",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("category", sa.String(length=64), nullable=True),
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("name", name="uq_skills_name"),
        )
        op.create_index("ix_skills_is_active", "skills", ["is_active"])

    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "reply_embeddings" not in tables:
        op.create_table(
            "reply_embeddings",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("draft_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("mailbox", sa.String(length=255), nullable=False),
            sa.Column("embedding", Vector(1536), nullable=False),
            sa.Column("reply_text", sa.Text(), nullable=False),
            sa.Column("original_email_preview", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["draft_id"],
                ["drafts.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("draft_id", name="uq_reply_embeddings_draft_id"),
        )
        op.create_index("ix_reply_embeddings_mailbox", "reply_embeddings", ["mailbox"])

    # CREATE INDEX CONCURRENTLY cannot run inside a transaction block.
    # Guard so a failed concurrent create can be retried after tables exist.
    inspector = sa.inspect(bind)
    existing = {i["name"] for i in inspector.get_indexes("reply_embeddings")}
    if _REPLY_HNSW_INDEX not in existing:
        with op.get_context().autocommit_block():
            op.create_index(
                _REPLY_HNSW_INDEX,
                "reply_embeddings",
                ["embedding"],
                unique=False,
                postgresql_using="hnsw",
                postgresql_ops={"embedding": "vector_cosine_ops"},
                postgresql_concurrently=True,
            )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            _REPLY_HNSW_INDEX,
            table_name="reply_embeddings",
            postgresql_concurrently=True,
        )

    op.drop_index("ix_reply_embeddings_mailbox", table_name="reply_embeddings")
    op.drop_table("reply_embeddings")

    op.drop_index("ix_skills_is_active", table_name="skills")
    op.drop_table("skills")

    op.drop_column("drafts", "suggested_actions")
    op.drop_column("drafts", "feedback_action")
    op.drop_column("drafts", "feedback_note")
