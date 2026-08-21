"""Add bcc_recipients on messages for Sent Items visibility.

Graph may expose bccRecipients on outbound mail; store separately from To/CC.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Must stay ≤32 chars — alembic_version.version_num is VARCHAR(32).
revision: str = "034_message_bcc"
down_revision: str | None = "033_chat_sessions"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column(
            "bcc_recipients",
            sa.ARRAY(sa.String(length=320)),
            nullable=False,
            server_default="{}",
        ),
    )
    op.alter_column("messages", "bcc_recipients", server_default=None)


def downgrade() -> None:
    op.drop_column("messages", "bcc_recipients")
