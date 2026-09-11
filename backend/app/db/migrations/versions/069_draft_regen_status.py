"""Persist draft regeneration running/failed/idle on threads.

Reload during rewrite must still show regenerating — status lives on the
thread row, not the browser.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "069_draft_regen_status"
down_revision: str | None = "068_draft_correct_actions"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("threads")}
    if "draft_regen_status" not in cols:
        op.add_column(
            "threads",
            sa.Column(
                "draft_regen_status",
                sa.String(length=16),
                nullable=False,
                server_default="idle",
            ),
        )
    if "draft_regen_error" not in cols:
        op.add_column(
            "threads",
            sa.Column("draft_regen_error", sa.Text(), nullable=True),
        )
    if "draft_regen_started_at" not in cols:
        op.add_column(
            "threads",
            sa.Column(
                "draft_regen_started_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("threads")}
    if "draft_regen_started_at" in cols:
        op.drop_column("threads", "draft_regen_started_at")
    if "draft_regen_error" in cols:
        op.drop_column("threads", "draft_regen_error")
    if "draft_regen_status" in cols:
        op.drop_column("threads", "draft_regen_status")
