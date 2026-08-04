"""Partial unique index on email_embeddings.message_id (race-safe upserts).

Drops the redundant non-unique ix_email_embeddings_message_id and replaces it
with a concurrent partial unique index so insert-on-conflict is safe under
concurrent embed of the same message.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "006_embed_msg_unique"
down_revision: str | None = "005_embeddings_hnsw"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_OLD_INDEX = "ix_email_embeddings_message_id"
_UNIQUE_INDEX = "uq_email_embeddings_message_id"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {i["name"] for i in inspector.get_indexes("email_embeddings")}

    # Keep one row per message_id (oldest by ctid) before unique index.
    op.execute(
        sa.text(
            """
            DELETE FROM email_embeddings a
            USING email_embeddings b
            WHERE a.message_id IS NOT NULL
              AND a.message_id = b.message_id
              AND a.ctid < b.ctid
            """
        )
    )

    with op.get_context().autocommit_block():
        if _OLD_INDEX in indexes:
            op.drop_index(
                _OLD_INDEX,
                table_name="email_embeddings",
                postgresql_concurrently=True,
            )
        # Re-inspect after possible drop (autocommit already applied).
        indexes = {i["name"] for i in sa.inspect(bind).get_indexes("email_embeddings")}
        if _UNIQUE_INDEX not in indexes:
            op.create_index(
                _UNIQUE_INDEX,
                "email_embeddings",
                ["message_id"],
                unique=True,
                postgresql_concurrently=True,
                postgresql_where=sa.text("message_id IS NOT NULL"),
            )


def downgrade() -> None:
    bind = op.get_bind()
    indexes = {i["name"] for i in sa.inspect(bind).get_indexes("email_embeddings")}

    with op.get_context().autocommit_block():
        if _UNIQUE_INDEX in indexes:
            op.drop_index(
                _UNIQUE_INDEX,
                table_name="email_embeddings",
                postgresql_concurrently=True,
            )
        indexes = {i["name"] for i in sa.inspect(bind).get_indexes("email_embeddings")}
        if _OLD_INDEX not in indexes:
            op.create_index(
                _OLD_INDEX,
                "email_embeddings",
                ["message_id"],
                unique=False,
                postgresql_concurrently=True,
            )
