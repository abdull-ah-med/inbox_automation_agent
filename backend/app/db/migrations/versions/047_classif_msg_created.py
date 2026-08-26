"""Composite (message_id, created_at DESC) for latest classification.

``classification_repo.get_latest_for_thread`` joins messages then orders by
classifications.created_at DESC. A single-column message_id index cannot
satisfy that order.

CREATE INDEX CONCURRENTLY per only-concurrent-indexes.md.
Revision id shortened: alembic_version.version_num is VARCHAR(32).

Revision ID: 047_classif_msg_created
Revises: 046_drafts_thread_created
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "047_classif_msg_created"
down_revision: str | None = "046_drafts_thread_created"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_TABLE = "classifications"
_INDEX = "ix_classifications_message_id_created_at"


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
                f"{_INDEX} ON {_TABLE} (message_id, created_at DESC)"
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
