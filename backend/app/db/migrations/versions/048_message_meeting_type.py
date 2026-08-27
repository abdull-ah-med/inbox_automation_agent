"""Store Graph meetingMessageType / responseType on messages.

Calendar accepts arrive with empty bodies. Graph identifies them via
eventMessage.meetingMessageType — we persist that instead of guessing
from subject prefixes.

Revision ID: 048_message_meeting_type
Revises: 047_classif_msg_created
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "048_message_meeting_type"
down_revision: str | None = "047_classif_msg_created"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("meeting_message_type", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "messages",
        sa.Column("meeting_response_type", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("messages", "meeting_response_type")
    op.drop_column("messages", "meeting_message_type")
