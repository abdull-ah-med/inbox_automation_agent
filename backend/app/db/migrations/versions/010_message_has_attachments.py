"""Add has_attachments on messages for dashboard attachment tags.

Graph already returns hasAttachments; persist it so the web UI can tag
each email that has attachments without re-calling Graph.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Must stay ≤32 chars — alembic_version.version_num is VARCHAR(32).
revision: str = "010_msg_has_attachments"
down_revision: str | None = "009_users_token_version"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column(
            "has_attachments",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.alter_column("messages", "has_attachments", server_default=None)


def downgrade() -> None:
    op.drop_column("messages", "has_attachments")
