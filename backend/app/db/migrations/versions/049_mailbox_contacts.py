"""Per-mailbox recipient aliases for draft salutations.

Stores reviewer-taught first/full names so drafts greet Kelvin Collado instead
of leaking the email local-part (Samplecontact). Cleartext storage matches
threads.subject — PII redaction applies to logs, not this table (see
project-context.mdc Data Privacy & Logging).

Revision ID: 049_mailbox_contacts
Revises: 048_message_meeting_type
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "049_mailbox_contacts"
down_revision: str | None = "048_message_meeting_type"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mailbox_contacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mailbox", sa.String(length=320), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("first_name", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mailbox", "email", name="uq_mailbox_contacts_mailbox_email"),
    )


def downgrade() -> None:
    op.drop_table("mailbox_contacts")
