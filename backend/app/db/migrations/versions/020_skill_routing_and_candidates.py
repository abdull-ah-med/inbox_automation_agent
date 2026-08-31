"""Add skill embeddings + skill_candidates for routing and reject promotion.

- skills.embedding vector(1536) nullable + optional HNSW
- skill_candidates table for human-gated skill proposals
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "020_skill_routing_candidates"
down_revision: str | None = "019_tone_profiles"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_SKILL_HNSW = "ix_skills_embedding_hnsw"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    skill_cols = {c["name"] for c in inspector.get_columns("skills")}
    if "embedding" not in skill_cols:
        op.add_column(
            "skills",
            sa.Column("embedding", Vector(1536), nullable=True),
        )

    tables = set(inspector.get_table_names())
    if "skill_candidates" not in tables:
        op.create_table(
            "skill_candidates",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("mailbox", sa.String(length=255), nullable=False),
            sa.Column("routing_category", sa.String(length=32), nullable=False),
            sa.Column("reason_code", sa.String(length=32), nullable=False),
            sa.Column("proposed_name", sa.String(length=255), nullable=False),
            sa.Column("proposed_content", sa.Text(), nullable=False),
            sa.Column(
                "source_rejection_ids",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column(
                "status",
                sa.String(length=16),
                nullable=False,
                server_default="pending",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_skill_candidates_mailbox", "skill_candidates", ["mailbox"])
        op.create_index(
            "ix_skill_candidates_bucket",
            "skill_candidates",
            ["mailbox", "routing_category", "reason_code"],
        )

    inspector = sa.inspect(bind)
    existing = {i["name"] for i in inspector.get_indexes("skills")}
    if _SKILL_HNSW not in existing:
        with op.get_context().autocommit_block():
            op.create_index(
                _SKILL_HNSW,
                "skills",
                ["embedding"],
                unique=False,
                postgresql_using="hnsw",
                postgresql_ops={"embedding": "vector_cosine_ops"},
                postgresql_concurrently=True,
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {i["name"] for i in inspector.get_indexes("skills")}
    if _SKILL_HNSW in existing:
        with op.get_context().autocommit_block():
            op.drop_index(_SKILL_HNSW, table_name="skills", postgresql_concurrently=True)

    if "skill_candidates" in inspector.get_table_names():
        op.drop_index("ix_skill_candidates_bucket", table_name="skill_candidates")
        op.drop_index("ix_skill_candidates_mailbox", table_name="skill_candidates")
        op.drop_table("skill_candidates")

    skill_cols = {c["name"] for c in sa.inspect(bind).get_columns("skills")}
    if "embedding" in skill_cols:
        op.drop_column("skills", "embedding")
