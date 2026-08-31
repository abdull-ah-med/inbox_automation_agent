"""Partial index on drafts.approved_at for recent-approved lookups.

``draft_repo.list_recent_approved_bodies`` orders by approved_at DESC where
the column is not null. A standalone thread_id btree cannot satisfy that.

CREATE INDEX CONCURRENTLY per only-concurrent-indexes.md.
Revision id shortened: alembic_version.version_num is VARCHAR(32).

Revision ID: 045_drafts_approved_at
Revises: 044_message_unique_body
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "045_drafts_approved_at"
down_revision: str | None = "044_message_unique_body"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_TABLE = "drafts"
_INDEX = "ix_drafts_approved_at"


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
                f"{_INDEX} ON {_TABLE} (approved_at DESC) "
                "WHERE approved_at IS NOT NULL"
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
