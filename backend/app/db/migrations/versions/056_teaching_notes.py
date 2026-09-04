"""Add teaching_notes table for human-authored and promoted-atom rules.

Teaching notes are the Cursor-rules-shaped surface: scoped prose injected into
the draft prompt when their applies_when gate passes.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "056_teaching_notes"
down_revision: str | None = "055_feedback_atoms"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_TABLE = "teaching_notes"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if _TABLE not in tables:
        op.create_table(
            _TABLE,
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("mailbox", sa.String(255), nullable=False),
            sa.Column("title", sa.String(200), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("applies_when", sa.Text(), nullable=True),
            sa.Column("scope", sa.String(24), nullable=False),
            sa.Column("scope_key", sa.String(320), nullable=False),
            sa.Column(
                "status",
                sa.String(16),
                nullable=False,
                server_default=sa.text("'active'"),
            ),
            sa.Column(
                "origin",
                sa.String(24),
                nullable=False,
                server_default=sa.text("'manual'"),
            ),
            sa.Column("origin_atom_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column(
                "person_bound",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
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
            sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
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
            sa.ForeignKeyConstraint(
                ["origin_atom_id"],
                ["feedback_atoms.id"],
                name="fk_teaching_notes_origin_atom",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_teaching_notes_mailbox_scope_key_status",
            _TABLE,
            ["mailbox", "scope", "scope_key", "status"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        return
    op.drop_index("ix_teaching_notes_mailbox_scope_key_status", table_name=_TABLE)
    op.drop_table(_TABLE)
