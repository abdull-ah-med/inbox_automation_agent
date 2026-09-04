"""Add feedback_atoms table for SLIFT-style atomic decomposition of feedback notes.

Each atom carries a Fix/Spec/Null role, an applies_when gate phrase, a scope
position on the ladder, and calibration counters.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "055_feedback_atoms"
down_revision: str | None = "054_preference_pairs"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_TABLE = "feedback_atoms"
_HNSW = "ix_feedback_atoms_embedding_hnsw"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if _TABLE not in tables:
        op.create_table(
            _TABLE,
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("source_kind", sa.String(32), nullable=False),
            sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("mailbox", sa.String(255), nullable=False),
            sa.Column("atom_text", sa.Text(), nullable=False),
            sa.Column("atom_embedding", Vector(1536), nullable=False),
            sa.Column("role", sa.String(8), nullable=False),
            sa.Column("applies_when", sa.Text(), nullable=True),
            sa.Column(
                "scope",
                sa.String(24),
                nullable=False,
                server_default=sa.text("'thread'"),
            ),
            sa.Column("scope_key", sa.String(320), nullable=False),
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            ),
            sa.Column(
                "hit_count",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "precision_num",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "precision_den",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "person_bound",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column("promoted_from_atom_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["promoted_from_atom_id"],
                ["feedback_atoms.id"],
                name="fk_feedback_atoms_promoted_from",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_feedback_atoms_mailbox_scope_key_active",
            _TABLE,
            ["mailbox", "scope", "scope_key", "is_active"],
        )
        op.create_index(
            "ix_feedback_atoms_scope_active",
            _TABLE,
            ["scope", "is_active"],
        )

    inspector = sa.inspect(bind)
    existing = {i["name"] for i in inspector.get_indexes(_TABLE)}
    if _HNSW not in existing:
        with op.get_context().autocommit_block():
            op.create_index(
                _HNSW,
                _TABLE,
                ["atom_embedding"],
                unique=False,
                postgresql_using="hnsw",
                postgresql_ops={"atom_embedding": "vector_cosine_ops"},
                postgresql_concurrently=True,
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        return
    existing = {i["name"] for i in inspector.get_indexes(_TABLE)}
    if _HNSW in existing:
        with op.get_context().autocommit_block():
            op.drop_index(_HNSW, table_name=_TABLE, postgresql_concurrently=True)
    op.drop_index("ix_feedback_atoms_scope_active", table_name=_TABLE)
    op.drop_index("ix_feedback_atoms_mailbox_scope_key_active", table_name=_TABLE)
    op.drop_table(_TABLE)
