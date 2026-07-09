from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class GraphNotificationItemSchema(BaseModel):
    subscription_id: str
    client_state: str | None = None
    change_type: Literal["created", "updated", "deleted"]
    resource: str
    subscription_expiration_date_time: datetime | None = None
    tenant_id: str | None = None


class GraphNotificationSchema(BaseModel):
    value: list[GraphNotificationItemSchema] = Field(default_factory=list)


class GraphValidationSchema(BaseModel):
    validation_token: str


class SimulateIngestRequestSchema(BaseModel):
    mailbox: str
    message_id: str
    conversation_id: str
    sender: str
    subject: str
    body_text: str
    body_preview: str | None = None
    received_at: datetime
