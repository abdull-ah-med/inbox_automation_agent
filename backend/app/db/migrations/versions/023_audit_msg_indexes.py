"""Add indexes for hot audit and message query patterns.

- audit_events (mailbox, conversation_id, created_at DESC) for triage flags
- audit_events (mailbox, created_at DESC) for recent activity
- messages (thread_id, received_at DESC) for latest-message windows

Created concurrently to avoid long write locks on populated tables.
See: https://www.postgresql.org/docs/current/sql-createindex.html#SQL-CREATEINDEX-CONCURRENTLY
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Must be <= 32 chars (alembic_version.version_num VARCHAR(32)).
revision: str = "023_audit_msg_indexes"
down_revision: str | None = "022_draft_applied_skills"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_AUDIT_MB_CONV = "ix_audit_mb_conv_created"
_AUDIT_MB_CREATED = "ix_audit_mailbox_created"
_MSG_THREAD_RECV = "ix_messages_thread_received"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    audit_indexes = {i["name"] for i in inspector.get_indexes("audit_events")}
    if _AUDIT_MB_CONV not in audit_indexes:
        with op.get_context().autocommit_block():
            op.create_index(
                _AUDIT_MB_CONV,
                "audit_events",
                ["mailbox", "conversation_id", "created_at"],
                unique=False,
                postgresql_concurrently=True,
            )

    inspector = sa.inspect(bind)
    audit_indexes = {i["name"] for i in inspector.get_indexes("audit_events")}
    if _AUDIT_MB_CREATED not in audit_indexes:
        with op.get_context().autocommit_block():
            op.create_index(
                _AUDIT_MB_CREATED,
                "audit_events",
                ["mailbox", "created_at"],
                unique=False,
                postgresql_concurrently=True,
            )

    inspector = sa.inspect(bind)
    message_indexes = {i["name"] for i in inspector.get_indexes("messages")}
    if _MSG_THREAD_RECV not in message_indexes:
        with op.get_context().autocommit_block():
            op.create_index(
                _MSG_THREAD_RECV,
                "messages",
                ["thread_id", "received_at"],
                unique=False,
                postgresql_concurrently=True,
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    message_indexes = {i["name"] for i in inspector.get_indexes("messages")}
    if _MSG_THREAD_RECV in message_indexes:
        with op.get_context().autocommit_block():
            op.drop_index(
                _MSG_THREAD_RECV,
                table_name="messages",
                postgresql_concurrently=True,
            )

    audit_indexes = {i["name"] for i in inspector.get_indexes("audit_events")}
    if _AUDIT_MB_CREATED in audit_indexes:
        with op.get_context().autocommit_block():
            op.drop_index(
                _AUDIT_MB_CREATED,
                table_name="audit_events",
                postgresql_concurrently=True,
            )

    audit_indexes = {i["name"] for i in sa.inspect(bind).get_indexes("audit_events")}
    if _AUDIT_MB_CONV in audit_indexes:
        with op.get_context().autocommit_block():
            op.drop_index(
                _AUDIT_MB_CONV,
                table_name="audit_events",
                postgresql_concurrently=True,
            )
