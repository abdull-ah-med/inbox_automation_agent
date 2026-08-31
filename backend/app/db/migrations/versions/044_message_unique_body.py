"""Store Graph uniqueBody as messages.unique_body_text for review display.

Nullable text — no table rewrite on PG 11+. Existing rows stay NULL until
the next ingest fill or the API read-time fallback.

Revision ID: 044_message_unique_body
Revises: 043_drop_redundant_indexes
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "044_message_unique_body"
down_revision: str | None = "043_drop_redundant_indexes"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("unique_body_text", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "unique_body_text")
