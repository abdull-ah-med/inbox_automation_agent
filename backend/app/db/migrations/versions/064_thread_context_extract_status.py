"""Track thread-context extract status so Rebuild survives refresh.

running/idle/failed plus started_at. Stale running (>5 min) is treated as failed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "064_ctx_extract_status"
down_revision: str | None = "063_thread_contexts_extract_hash"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_TABLE = "thread_contexts"


def _columns(inspector: sa.Inspector, table: str) -> set[str]:
    if table not in inspector.get_table_names():
        return set()
    return {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = _columns(inspector, _TABLE)
    if not cols:
        return
    if "extract_status" not in cols:
        op.add_column(
            _TABLE,
            sa.Column(
                "extract_status",
                sa.String(16),
                nullable=False,
                server_default="idle",
            ),
        )
    if "extract_started_at" not in cols:
        op.add_column(
            _TABLE,
            sa.Column("extract_started_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "last_extract_error" not in cols:
        op.add_column(
            _TABLE,
            sa.Column("last_extract_error", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    return
