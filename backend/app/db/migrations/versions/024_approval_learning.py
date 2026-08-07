"""Add approval learning context fields on drafts and reply_embeddings.

- drafts.approval_note / approval_scope / approval_note_persisted_at
- reply_embeddings.learning_note
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Must be <= 32 chars (alembic_version.version_num VARCHAR(32)).
revision: str = "024_approval_learning"
down_revision: str | None = "023_audit_msg_indexes"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_DRAFT_COLS = (
    ("approval_note", sa.Text(), True),
    ("approval_scope", sa.String(length=16), True),
    ("approval_note_persisted_at", sa.DateTime(timezone=True), True),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    draft_cols = {c["name"] for c in inspector.get_columns("drafts")}
    for name, col_type, nullable in _DRAFT_COLS:
        if name not in draft_cols:
            op.add_column("drafts", sa.Column(name, col_type, nullable=nullable))

    reply_cols = {c["name"] for c in inspector.get_columns("reply_embeddings")}
    if "learning_note" not in reply_cols:
        op.add_column(
            "reply_embeddings",
            sa.Column("learning_note", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    reply_cols = {c["name"] for c in inspector.get_columns("reply_embeddings")}
    if "learning_note" in reply_cols:
        op.drop_column("reply_embeddings", "learning_note")

    draft_cols = {c["name"] for c in inspector.get_columns("drafts")}
    for name, _, _ in reversed(_DRAFT_COLS):
        if name in draft_cols:
            op.drop_column("drafts", name)
