"""Backfill leftover NO_ACTION rows to RESOLVED (DraftAssistant auto-close).

Revision ID: 053_no_action_to_resolved
Revises: 052_global_contacts
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "053_no_action_to_resolved"
down_revision: str | None = "052_global_contacts"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text("UPDATE threads SET state = 'RESOLVED' WHERE state = 'NO_ACTION'")
    )


def downgrade() -> None:
    # Irreversible: courtesy/FYI discards were mixed into NO_ACTION historically.
    pass
