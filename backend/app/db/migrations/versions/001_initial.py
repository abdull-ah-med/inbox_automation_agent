"""Initial schema: threads, messages, classifications, drafts, audit, embeddings, links."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "001_initial"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

NOW = sa.text("now()")
JSONB = sa.dialects.postgresql.JSONB
ARRAY = sa.dialects.postgresql.ARRAY


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "threads",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("mailbox", sa.String(length=320), nullable=False),
        sa.Column("conversation_id", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=998), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("urgency", sa.String(length=16), nullable=True),
        sa.Column("category", sa.String(length=32), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_updated_at",
            sa.DateTime(timezone=True),
            server_default=NOW,
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "mailbox",
            "conversation_id",
            name="uq_threads_mailbox_conversation",
        ),
    )
    op.create_index("ix_threads_mailbox", "threads", ["mailbox"])
    op.create_index("ix_threads_conversation_id", "threads", ["conversation_id"])

    op.create_table(
        "messages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("thread_id", sa.UUID(), nullable=False),
        sa.Column("graph_message_id", sa.String(length=255), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("sender", sa.String(length=320), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("body_preview", sa.Text(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["thread_id"], ["threads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("graph_message_id"),
    )
    op.create_index("ix_messages_thread_id", "messages", ["thread_id"])

    op.create_table(
        "classifications",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("message_id", sa.UUID(), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("intent", sa.String(length=32), nullable=False),
        sa.Column("urgency", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("entities", JSONB(), nullable=False),
        sa.Column("model_version", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=NOW,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_classifications_message_id", "classifications", ["message_id"])

    op.create_table(
        "drafts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("thread_id", sa.UUID(), nullable=False),
        sa.Column("subject", sa.String(length=998), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("recipients", JSONB(), nullable=False),
        sa.Column("teaching_note", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("urgency", sa.String(length=16), nullable=True),
        sa.Column("urgency_reason", sa.Text(), nullable=True),
        sa.Column("context_match_confidence", sa.Float(), nullable=True),
        sa.Column("edited_body", sa.Text(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=NOW,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["thread_id"], ["threads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_drafts_thread_id", "drafts", ["thread_id"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=255), nullable=False),
        sa.Column("mailbox", sa.String(length=320), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=NOW,
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])
    op.create_index("ix_audit_events_conversation_id", "audit_events", ["conversation_id"])

    op.create_table(
        "email_embeddings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("message_id", sa.UUID(), nullable=True),
        sa.Column("mailbox", sa.String(length=320), nullable=False),
        sa.Column("conversation_id", sa.String(length=255), nullable=False),
        sa.Column("sender_email", sa.String(length=320), nullable=False),
        sa.Column("recipient_emails", ARRAY(sa.String(length=320)), nullable=False),
        sa.Column(
            "cc_emails",
            ARRAY(sa.String(length=320)),
            nullable=False,
            server_default=sa.text("'{}'::varchar(320)[]"),
        ),
        sa.Column("embedding", Vector(1536), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("body_preview", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_email_embeddings_mailbox", "email_embeddings", ["mailbox"])
    op.create_index("ix_email_embeddings_conversation_id", "email_embeddings", ["conversation_id"])
    op.create_index("ix_email_embeddings_message_id", "email_embeddings", ["message_id"])
    op.create_index("ix_email_embeddings_sender_email", "email_embeddings", ["sender_email"])

    op.create_table(
        "thread_links",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("source_message_id", sa.UUID(), nullable=False),
        sa.Column("matched_embedding_id", sa.UUID(), nullable=False),
        sa.Column("matched_conversation_id", sa.String(length=255), nullable=False),
        sa.Column("similarity_score", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=NOW,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["source_message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["matched_embedding_id"],
            ["email_embeddings.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_thread_links_source_message_id", "thread_links", ["source_message_id"])
    op.create_index(
        "ix_thread_links_matched_embedding_id",
        "thread_links",
        ["matched_embedding_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_thread_links_matched_embedding_id", table_name="thread_links")
    op.drop_index("ix_thread_links_source_message_id", table_name="thread_links")
    op.drop_table("thread_links")
    op.drop_index("ix_email_embeddings_sender_email", table_name="email_embeddings")
    op.drop_index("ix_email_embeddings_message_id", table_name="email_embeddings")
    op.drop_index("ix_email_embeddings_conversation_id", table_name="email_embeddings")
    op.drop_index("ix_email_embeddings_mailbox", table_name="email_embeddings")
    op.drop_table("email_embeddings")
    op.drop_index("ix_audit_events_conversation_id", table_name="audit_events")
    op.drop_index("ix_audit_events_event_type", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_drafts_thread_id", table_name="drafts")
    op.drop_table("drafts")
    op.drop_index("ix_classifications_message_id", table_name="classifications")
    op.drop_table("classifications")
    op.drop_index("ix_messages_thread_id", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_threads_conversation_id", table_name="threads")
    op.drop_index("ix_threads_mailbox", table_name="threads")
    op.drop_table("threads")
