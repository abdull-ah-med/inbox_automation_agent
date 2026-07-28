"""Index threads by mailbox + last_message_at for dashboard listings.

Created concurrently to avoid long write locks on a populated threads table.
See: https://www.postgresql.org/docs/current/sql-createindex.html#SQL-CREATEINDEX-CONCURRENTLY
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Must be <= 32 chars (alembic_version.version_num VARCHAR(32)).
revision: str = "008_threads_mb_lma_idx"
down_revision: str | None = "007_users_refresh_tokens"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_INDEX = "ix_threads_mailbox_last_message_at"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {i["name"] for i in inspector.get_indexes("threads")}
    if _INDEX in indexes:
        return

    # CREATE INDEX CONCURRENTLY cannot run inside a transaction block.
    with op.get_context().autocommit_block():
        op.create_index(
            _INDEX,
            "threads",
            ["mailbox", "last_message_at"],
            unique=False,
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {i["name"] for i in inspector.get_indexes("threads")}
    if _INDEX not in indexes:
        return

    with op.get_context().autocommit_block():
        op.drop_index(
            _INDEX,
            table_name="threads",
            postgresql_concurrently=True,
        )
