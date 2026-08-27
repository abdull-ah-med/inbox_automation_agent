"""Numeric-slack signature and sender for alert near-match without split_part.

Revision ID: 042_alert_signature
Revises: 041_alert_fingerprint
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "042_alert_signature"
down_revision: str | None = "041_alert_fingerprint"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("threads", sa.Column("alert_signature", sa.Text(), nullable=True))
    op.add_column("threads", sa.Column("alert_sender_norm", sa.String(length=320), nullable=True))
    op.create_index(
        "ix_threads_mailbox_alert_signature_last_message_at",
        "threads",
        ["mailbox", "alert_sender_norm", "alert_signature", "last_message_at"],
        postgresql_where=sa.text("alert_signature IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_threads_mailbox_alert_signature_last_message_at",
        table_name="threads",
    )
    op.drop_column("threads", "alert_sender_norm")
    op.drop_column("threads", "alert_signature")
