"""Replace 4-col teaching-notes index with 3-col (mailbox, scope, scope_key).

Mirrors 060's atom index: retrieval filters on mailbox + scope + scope_key;
status is applied as a residual predicate, not a leading index column.

Concurrent indexes run in autocommit_block (only-concurrent-indexes).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "061_teaching_notes_scope_index"
down_revision: str | None = "060_feedback_loops_integrity"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_NOTES = "teaching_notes"
_IX_OLD = "ix_teaching_notes_mailbox_scope_key_status"
_IX_NEW = "ix_teaching_notes_mailbox_scope_key"


def _indexes(inspector: sa.Inspector, table: str) -> set[str]:
    if table not in inspector.get_table_names():
        return set()
    return {i["name"] for i in inspector.get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    with op.get_context().autocommit_block():
        inspector = sa.inspect(bind)
        names = _indexes(inspector, _NOTES)
        if _IX_NEW not in names:
            op.create_index(
                _IX_NEW,
                _NOTES,
                ["mailbox", "scope", "scope_key"],
                unique=False,
                postgresql_concurrently=True,
            )
        inspector = sa.inspect(bind)
        names = _indexes(inspector, _NOTES)
        if _IX_OLD in names:
            op.drop_index(
                _IX_OLD,
                table_name=_NOTES,
                postgresql_concurrently=True,
            )


def downgrade() -> None:
    bind = op.get_bind()
    with op.get_context().autocommit_block():
        inspector = sa.inspect(bind)
        names = _indexes(inspector, _NOTES)
        if _IX_OLD not in names:
            op.create_index(
                _IX_OLD,
                _NOTES,
                ["mailbox", "scope", "scope_key", "status"],
                unique=False,
                postgresql_concurrently=True,
            )
        inspector = sa.inspect(bind)
        names = _indexes(inspector, _NOTES)
        if _IX_NEW in names:
            op.drop_index(_IX_NEW, table_name=_NOTES, postgresql_concurrently=True)
