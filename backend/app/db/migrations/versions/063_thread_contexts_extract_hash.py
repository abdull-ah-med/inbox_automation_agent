"""Add missing Plan 3 columns when thread_contexts already existed.

062 skipped create_table if the table was present. An earlier pointer
table had no extract_input_hash, so GET /context 500ed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "063_thread_contexts_extract_hash"
down_revision: str | None = "062_thread_contexts"
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
    if "extract_input_hash" not in cols:
        op.add_column(
            _TABLE,
            sa.Column(
                "extract_input_hash",
                sa.String(64),
                nullable=False,
                server_default="",
            ),
        )
    if "last_message_id_at_extract" not in cols:
        op.add_column(
            _TABLE,
            sa.Column(
                "last_message_id_at_extract",
                postgresql.UUID(as_uuid=True),
                nullable=True,
            ),
        )
        fks = {fk["name"] for fk in inspector.get_foreign_keys(_TABLE)}
        if "fk_thread_contexts_last_message" not in fks:
            op.create_foreign_key(
                "fk_thread_contexts_last_message",
                _TABLE,
                "messages",
                ["last_message_id_at_extract"],
                ["id"],
                ondelete="SET NULL",
            )


def downgrade() -> None:
    # Repair-only. Do not drop columns 062 may have created on a complete table.
    return
