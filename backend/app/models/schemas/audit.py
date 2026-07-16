"""Audit event schemas — metadata-only payloads (no email bodies)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AuditEventSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_type: str
    conversation_id: str
    mailbox: str
    payload: dict[str, Any] = Field(default_factory=dict)
    actor: str = "system"
    created_at: datetime
