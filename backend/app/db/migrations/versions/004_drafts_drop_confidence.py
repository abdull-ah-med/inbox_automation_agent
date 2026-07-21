"""Drop drafts.confidence; add message_id for idempotent draft lookup.

Urgency / urgency_reason / context_match_confidence already exist from 001.
Revision id must stay ≤32 chars (alembic_version.version_num VARCHAR(32)).
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
            sa.Column(
                "message_id",
                sa.String(length=512),
                nullable=False,
                server_default="",
            ),
        )
        op.alter_column("drafts", "message_id", server_default=None)

    if "uq_drafts_message_id" not in indexes:
        op.create_index(
            "uq_drafts_message_id",
            "drafts",
            ["message_id"],
            unique=True,
        )

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
        op.drop_index("uq_drafts_message_id", table_name="drafts")

    if "message_id" in columns:
        op.drop_column("drafts", "message_id")
