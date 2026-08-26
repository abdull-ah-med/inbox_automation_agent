"""Composite (thread_id, created_at DESC) for latest teaching notes.

``draft_repo.latest_teaching_notes_by_threads`` groups by thread_id and takes
max(created_at). A single-column thread_id index cannot satisfy the order.

CREATE INDEX CONCURRENTLY per only-concurrent-indexes.md.
Revision id shortened: alembic_version.version_num is VARCHAR(32).

Revision ID: 046_drafts_thread_created
Revises: 045_drafts_approved_at
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "046_drafts_thread_created"
down_revision: str | None = "045_drafts_approved_at"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_TABLE = "drafts"
_INDEX = "ix_drafts_thread_id_created_at"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        return
    names = {i["name"] for i in inspector.get_indexes(_TABLE)}
    if _INDEX in names:
        return
    with op.get_context().autocommit_block():
        op.execute(
            sa.text(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                f"{_INDEX} ON {_TABLE} (thread_id, created_at DESC)"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        return
    names = {i["name"] for i in inspector.get_indexes(_TABLE)}
    if _INDEX not in names:
        return
    with op.get_context().autocommit_block():
        op.drop_index(_INDEX, table_name=_TABLE, postgresql_concurrently=True)
