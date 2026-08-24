"""Composite (mailbox, sent_at DESC) for FTS recency fallback.

Revision ID: 039_embed_mailbox_sent_at
Revises: 038_chat_cache_scope_index

search_fts with empty query text orders by sent_at DESC scoped by mailbox.
A standalone mailbox btree cannot satisfy that order.

Revision id shortened: alembic_version.version_num is VARCHAR(32).

See SQLAlchemy/Alembic ``only-concurrent-indexes``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "039_embed_mailbox_sent_at"
down_revision: str | None = "038_chat_cache_scope_index"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_TABLE = "email_embeddings"
_INDEX = "ix_email_embeddings_mailbox_sent_at"


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
                f"{_INDEX} ON {_TABLE} (mailbox, sent_at DESC)"
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
