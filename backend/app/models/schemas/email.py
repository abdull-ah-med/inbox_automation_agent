from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ThreadStateEnum(StrEnum):
    NEW = "NEW"
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


class ThreadContextSchema(BaseModel):
    conversation_id: str
    mailbox: str
    subject: str
    messages: list[EmailMessageSchema] = Field(default_factory=list)
