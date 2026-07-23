"""Drop drafts.confidence; add message_id for idempotent draft lookup.

Urgency / urgency_reason / context_match_confidence already exist from 001.
Revision id must stay ≤32 chars (alembic_version.version_num VARCHAR(32)).

``message_id`` is backfilled with unique ``legacy:<uuid>`` placeholders when
missing so the unique index never collides on empty strings. Unique index is
created CONCURRENTLY to avoid long write locks.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "004_drafts_drop_confidence"
down_revision: str | None = "003_message_recipients"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("drafts")}
    indexes = {i["name"] for i in inspector.get_indexes("drafts")}

    if "urgency" not in columns:
        op.add_column(
            "drafts",
            sa.Column("urgency", sa.String(length=16), nullable=True),
        )
    if "urgency_reason" not in columns:
        op.add_column(
            "drafts",
            sa.Column("urgency_reason", sa.Text(), nullable=True),
        )
    if "context_match_confidence" not in columns:
        op.add_column(
            "drafts",
            sa.Column("context_match_confidence", sa.Float(), nullable=True),
        )

    if "message_id" not in columns:
        op.add_column(
            "drafts",
            sa.Column("message_id", sa.String(length=512), nullable=True),
        )

    # Unique placeholders for any null/empty rows before NOT NULL + unique index.
    op.execute(
        sa.text(
            """
            UPDATE drafts
            SET message_id = 'legacy:' || id::text
            WHERE message_id IS NULL OR btrim(message_id) = ''
            """
        )
    )
    op.alter_column(
        "drafts",
        "message_id",
        existing_type=sa.String(length=512),
        nullable=False,
        server_default=None,
    )

    if "uq_drafts_message_id" not in indexes:
        # CREATE INDEX CONCURRENTLY cannot run inside a transaction block.
        with op.get_context().autocommit_block():
            op.create_index(
                "uq_drafts_message_id",
                "drafts",
                ["message_id"],
                unique=True,
                postgresql_concurrently=True,
            )

    # Refresh column set after possible adds.
    columns = {c["name"] for c in sa.inspect(bind).get_columns("drafts")}
    if "confidence" in columns:
        op.drop_column("drafts", "confidence")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("drafts")}
    indexes = {i["name"] for i in inspector.get_indexes("drafts")}

    if "confidence" not in columns:
        op.add_column(
            "drafts",
            sa.Column("confidence", sa.Float(), nullable=True),
        )

    if "uq_drafts_message_id" in indexes:
        with op.get_context().autocommit_block():
            op.drop_index(
                "uq_drafts_message_id",
                table_name="drafts",
                postgresql_concurrently=True,
            )

    columns = {c["name"] for c in sa.inspect(bind).get_columns("drafts")}
    if "message_id" in columns:
        op.drop_column("drafts", "message_id")
