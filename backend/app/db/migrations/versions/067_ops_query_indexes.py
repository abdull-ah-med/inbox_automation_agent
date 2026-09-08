"""Concurrent indexes for ops window queries and admin lists.

- drafts.rejected_at (partial) for reject-theme windows
- threads (mailbox, state) for queue snapshots
- threads last_updated_at where RESOLVED for scope decay
- feedback_atoms / teaching_notes (mailbox, created_at DESC) for admin lists

CREATE INDEX CONCURRENTLY per only-concurrent-indexes.
Revision id shortened: alembic_version.version_num is VARCHAR(32).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "067_ops_query_indexes"
down_revision: str | None = "066_global_scope_key_indexes"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_INDEXES: tuple[tuple[str, str, str], ...] = (
    (
        "ix_drafts_rejected_at",
        "drafts",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_drafts_rejected_at "
        "ON drafts (rejected_at DESC) WHERE rejected_at IS NOT NULL",
    ),
    (
        "ix_threads_mailbox_state",
        "threads",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_threads_mailbox_state "
        "ON threads (mailbox, state)",
    ),
    (
        "ix_threads_resolved_last_updated_at",
        "threads",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_threads_resolved_last_updated_at "
        "ON threads (last_updated_at) WHERE state = 'RESOLVED'",
    ),
    (
        "ix_feedback_atoms_mailbox_created_at",
        "feedback_atoms",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_feedback_atoms_mailbox_created_at "
        "ON feedback_atoms (mailbox, created_at DESC)",
    ),
    (
        "ix_teaching_notes_mailbox_created_at",
        "teaching_notes",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_teaching_notes_mailbox_created_at "
        "ON teaching_notes (mailbox, created_at DESC)",
    ),
)


def _indexes(inspector: sa.Inspector, table: str) -> set[str]:
    if table not in inspector.get_table_names():
        return set()
    return {i["name"] for i in inspector.get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    with op.get_context().autocommit_block():
        inspector = sa.inspect(bind)
        for name, table, ddl in _INDEXES:
            if name in _indexes(inspector, table):
                continue
            if table not in inspector.get_table_names():
                continue
            op.execute(sa.text(ddl))
            inspector = sa.inspect(bind)


def downgrade() -> None:
    bind = op.get_bind()
    with op.get_context().autocommit_block():
        inspector = sa.inspect(bind)
        for name, table, _ddl in reversed(_INDEXES):
            if name not in _indexes(inspector, table):
                continue
            op.drop_index(name, table_name=table, postgresql_concurrently=True)
            inspector = sa.inspect(bind)
