"""Scope thread uniqueness to (mailbox, conversation_id) for DBs created from early 001.

Fresh installs already get the composite unique from 001_initial. This migration
is idempotent for environments that applied the old global conversation_id unique.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Must stay ≤32 chars — alembic_version.version_num is VARCHAR(32).
revision: str = "002_mailbox_conversation_uq"
down_revision: str | None = "001_initial"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    unique_names = {u["name"] for u in inspector.get_unique_constraints("threads")}
    index_names = {i["name"] for i in inspector.get_indexes("threads")}

    if "threads_conversation_id_key" in unique_names:
        op.drop_constraint("threads_conversation_id_key", "threads", type_="unique")

    if "uq_threads_mailbox_conversation" not in unique_names:
        op.create_unique_constraint(
            "uq_threads_mailbox_conversation",
            "threads",
            ["mailbox", "conversation_id"],
        )

    if "ix_threads_conversation_id" not in index_names:
        op.create_index("ix_threads_conversation_id", "threads", ["conversation_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    unique_names = {u["name"] for u in inspector.get_unique_constraints("threads")}
    index_names = {i["name"] for i in inspector.get_indexes("threads")}

    if "ix_threads_conversation_id" in index_names:
        op.drop_index("ix_threads_conversation_id", table_name="threads")
    if "uq_threads_mailbox_conversation" in unique_names:
        op.drop_constraint("uq_threads_mailbox_conversation", "threads", type_="unique")
    if "threads_conversation_id_key" not in unique_names:
        op.create_unique_constraint(
            "threads_conversation_id_key",
            "threads",
            ["conversation_id"],
        )
