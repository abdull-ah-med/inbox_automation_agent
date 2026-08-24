"""Chat cache retention: CHECK expires_at > created_at and query-hash unique.

Revision ID: 040_chat_cache_retention
Revises: 039_embed_mailbox_sent_at

Option B of the audit H12: keep answers in Postgres but shorten the default
search TTL (application setting) and prevent duplicate/illogical rows.
CHECK is added NOT VALID then validated so existing rows are scanned without
a long ACCESS EXCLUSIVE rewrite. Unique is a concurrent index + constraint.

See SQLAlchemy/Alembic ``split-check-constraint`` and ``unique-constraint``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "040_chat_cache_retention"
down_revision: str | None = "039_embed_mailbox_sent_at"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_TABLE = "chat_response_cache"
_CHECK = "ck_chat_response_cache_expires_after_created"
_UQ_INDEX = "uq_chat_response_cache_scope_query_hash"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        return
    op.execute(
        sa.text(
            f"ALTER TABLE {_TABLE} ADD CONSTRAINT {_CHECK} "
            "CHECK (expires_at > created_at) NOT VALID"
        )
    )
    op.execute(sa.text(f"ALTER TABLE {_TABLE} VALIDATE CONSTRAINT {_CHECK}"))
    names = {i["name"] for i in inspector.get_indexes(_TABLE)}
    if _UQ_INDEX not in names:
        with op.get_context().autocommit_block():
            op.execute(
                sa.text(
                    f"CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {_UQ_INDEX} "
                    f"ON {_TABLE} (mailbox_key, user_key, md5(query_normalized))"
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        return
    names = {i["name"] for i in inspector.get_indexes(_TABLE)}
    if _UQ_INDEX in names:
        with op.get_context().autocommit_block():
            op.drop_index(_UQ_INDEX, table_name=_TABLE, postgresql_concurrently=True)
    op.execute(sa.text(f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_CHECK}"))
