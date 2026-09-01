"""Make mailbox_contacts global by email (shared across mailboxes).

Dedupes existing (mailbox, email) rows keeping the newest updated_at, then
switches uniqueness from (mailbox, email) to email alone. The mailbox column
remains as last-touch metadata for where the name was last saved.

Revision ID: 052_global_contacts
Revises: 051_sender_name_automated
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "052_global_contacts"
down_revision: str | None = "051_sender_name_automated"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # Keep one row per email (newest updated_at wins; tie-break on id).
    op.execute(
        sa.text(
            """
            DELETE FROM mailbox_contacts AS mc
            WHERE mc.id IN (
                SELECT id
                FROM (
                    SELECT
                        id,
                        ROW_NUMBER() OVER (
                            PARTITION BY lower(email)
                            ORDER BY updated_at DESC, id DESC
                        ) AS rn
                    FROM mailbox_contacts
                ) ranked
                WHERE ranked.rn > 1
            )
            """
        )
    )
    op.drop_constraint(
        "uq_mailbox_contacts_mailbox_email",
        "mailbox_contacts",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_mailbox_contacts_email",
        "mailbox_contacts",
        ["email"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_mailbox_contacts_email",
        "mailbox_contacts",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_mailbox_contacts_mailbox_email",
        "mailbox_contacts",
        ["mailbox", "email"],
    )
