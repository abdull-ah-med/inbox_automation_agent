"""Drop redundant single-column indexes covered by composites.

PostgreSQL can satisfy leading-column predicates from a multicolumn B-tree
(https://www.postgresql.org/docs/current/indexes-multicolumn.html), so these
solo indexes only add write amplification:

- ix_spam_allowlist_mailbox ← uq_spam_allowlist_mailbox_sender
- ix_threads_mailbox ← uq_threads_mailbox_conversation (+ ix_threads_mailbox_last_message_at)
- ix_thread_association_reviews_source_thread_id ← uq_thread_association_reviews_pair
- ix_chat_sessions_user_id ← ix_chat_sessions_user_last_message

DROP INDEX CONCURRENTLY per project rule only-concurrent-indexes.md and
https://www.postgresql.org/docs/current/sql-dropindex.html

Revision ID: 043_drop_redundant_indexes
Revises: 042_alert_signature
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "043_drop_redundant_indexes"
down_revision: str | None = "042_alert_signature"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

# (table, index_name, recreate_columns for downgrade)
_INDEXES: tuple[tuple[str, str, list[str]], ...] = (
    ("spam_allowlist", "ix_spam_allowlist_mailbox", ["mailbox"]),
    ("threads", "ix_threads_mailbox", ["mailbox"]),
    (
        "thread_association_reviews",
        "ix_thread_association_reviews_source_thread_id",
        ["source_thread_id"],
    ),
    ("chat_sessions", "ix_chat_sessions_user_id", ["user_id"]),
)


def upgrade() -> None:
    bind = op.get_bind()
    for table, name, _cols in _INDEXES:
        inspector = sa.inspect(bind)
        existing = {idx["name"] for idx in inspector.get_indexes(table)}
        if name not in existing:
            continue
        with op.get_context().autocommit_block():
            op.drop_index(name, table_name=table, postgresql_concurrently=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table, name, cols in _INDEXES:
        inspector = sa.inspect(bind)
        existing = {idx["name"] for idx in inspector.get_indexes(table)}
        if name in existing:
            continue
        with op.get_context().autocommit_block():
            op.create_index(
                name,
                table,
                cols,
                postgresql_concurrently=True,
            )
