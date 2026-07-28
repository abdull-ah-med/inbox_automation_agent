from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ThreadStateEnum(StrEnum):
    """Thread lifecycle state, written by the pipeline after each triage/draft step.

    ``NEW`` is transient (message ingested, not yet triaged). ``SPAM`` and
    ``NO_ACTION`` are terminal triage outcomes (mirrors the
    ``triage.spam_discarded`` / ``triage.no_action_discarded`` audit event
    names) — excluded from the default actionable views. ``REQUIRES_HUMAN``
    mirrors ``EmailTriageState.draft_status`` for triage-passed messages where
    draft generation itself failed. ``DRAFTED`` / ``AWAITING_*`` / ``RESOLVED``
    are the real actionable states.
    """

    NEW = "NEW"
    SPAM = "SPAM"
    NO_ACTION = "NO_ACTION"
    REQUIRES_HUMAN = "REQUIRES_HUMAN"
    DRAFTED = "DRAFTED"
    AWAITING_CLIENT = "AWAITING_CLIENT"
    AWAITING_VENDOR = "AWAITING_VENDOR"
    AWAITING_PARTNER = "AWAITING_PARTNER"
    RESOLVED = "RESOLVED"


class EmailDirectionEnum(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class EmailMessageSchema(BaseModel):
    message_id: str
    conversation_id: str
    mailbox: str
    sender: str
    subject: str
    body_text: str
    body_preview: str | None = None
    received_at: datetime
    direction: EmailDirectionEnum = EmailDirectionEnum.INBOUND
    to_recipients: list[str] = Field(default_factory=list)
    cc_recipients: list[str] = Field(default_factory=list)
    has_attachments: bool = False


class ThreadContextSchema(BaseModel):
    conversation_id: str
    mailbox: str
    subject: str
    messages: list[EmailMessageSchema] = Field(default_factory=list)
