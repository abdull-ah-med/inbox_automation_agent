"""B-tree on chat_response_cache (mailbox_key, user_key) for ANN prefilter.

Revision ID: 038_chat_cache_scope_index
Revises: 037_pg_trgm_like_filters

find_semantic_hit filters mailbox_key + user_key then orders by cosine.
Migration 032 only indexes (mailbox_key, expires_at) and HNSW on the
embedding, so the HNSW output was filtered after the scan.

See SQLAlchemy/Alembic ``only-concurrent-indexes`` and
``verify-query-patterns-are-indexed``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "038_chat_cache_scope_index"
down_revision: str | None = "037_pg_trgm_like_filters"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_TABLE = "chat_response_cache"
_INDEX = "ix_chat_response_cache_mailbox_user"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        return
    names = {i["name"] for i in inspector.get_indexes(_TABLE)}
    if _INDEX in names:
        return
    with op.get_context().autocommit_block():
        op.create_index(
            _INDEX,
            _TABLE,
            ["mailbox_key", "user_key"],
            unique=False,
            postgresql_concurrently=True,
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
