"""Add to_recipients / cc_recipients on messages for triage retry accuracy.

Haiku triage decides has_action_items using To/CC vs the monitored mailbox.
Those lists must survive Redis-claim expiry and ``retry_triage`` rebuilds.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Must stay ≤32 chars — alembic_version.version_num is VARCHAR(32).
revision: str = "003_message_recipients"
down_revision: str | None = "002_mailbox_conversation_uq"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column(
            "to_recipients",
            sa.ARRAY(sa.String(length=320)),
            nullable=False,
            server_default="{}",
        ),
    )
    op.add_column(
        "messages",
        sa.Column(
            "cc_recipients",
            sa.ARRAY(sa.String(length=320)),
            nullable=False,
            server_default="{}",
        ),
    )
    # Drop server defaults after backfill so inserts must supply values explicitly.
    op.alter_column("messages", "to_recipients", server_default=None)
    op.alter_column("messages", "cc_recipients", server_default=None)


def downgrade() -> None:
    op.drop_column("messages", "cc_recipients")
    op.drop_column("messages", "to_recipients")
