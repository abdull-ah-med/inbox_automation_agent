"""Dashboard and mailbox API response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MailboxKey = Literal["client-relations", "sales", "vendor", "intermediary"]

MAILBOX_KEYS: tuple[MailboxKey, ...] = (
    "client-relations",
    "sales",
    "vendor",
    "intermediary",
)


class AuditEntry(BaseModel):
    timestamp: datetime
    event: str
    detail: str
    source: str


class TriageFlags(BaseModel):
    """Latest Haiku triage snapshot for a thread (from audit payload)."""

    is_spam: bool | None = None
    has_action_items: bool | None = None
    needs_context: bool | None = None
    spam_reason: str | None = None
    context_reason: str | None = None
    action_items_summary: str | None = None
    outcome: str | None = None


class ThreadSummary(BaseModel):
    id: uuid.UUID
    mailbox: str
    mailbox_key: MailboxKey | str
    subject: str
    state: str
    urgency: str | None = None
    category: str | None = None
    last_message_at: datetime | None = None
    last_sender: str | None = None
    preview: str | None = None
    staleness_hours: float = 0.0
    message_count: int = 0
    has_draft: bool = False
    teaching_note: str | None = None
    triage: TriageFlags | None = None
    outlook_url: str | None = None


class MailboxOverview(BaseModel):
    mailbox: str
    email_address: str
    label: str = ""
    thread_count: int = 0
    unread_count: int = 0
    awaiting_action_count: int = 0
    filtered_count: int = 0
    stale_count: int = 0
    urgency_breakdown: dict[str, int] = Field(default_factory=dict)
    recent_threads: list[ThreadSummary] = Field(default_factory=list)


class DashboardOverview(BaseModel):
    mailboxes: list[MailboxOverview]
    total_threads: int
    total_awaiting: int
    total_stale: int
    needs_attention: list[ThreadSummary] = Field(default_factory=list)
    recent_activity: list[AuditEntry] = Field(default_factory=list)
    updated_at: datetime


class ThreadList(BaseModel):
    items: list[ThreadSummary]
    next_cursor: str | None = None


class MessageDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    direction: str
    sender: str
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    body_text: str
    body_preview: str | None = None
    received_at: datetime
    has_attachments: bool = False
    outlook_url: str | None = None


class ClassificationView(BaseModel):
    category: str
    intent: str
    urgency: str
    confidence: float | None = None
    entities: dict[str, Any] = Field(default_factory=dict)
    model_version: str
    created_at: datetime


class SuggestedActionView(BaseModel):
    step: int
    action: str
    stakeholder: str | None = None
    rationale: str


class DraftView(BaseModel):
    id: uuid.UUID
    subject: str
    body: str
    teaching_note: str
    urgency: str | None = None
    urgency_reason: str | None = None
    forward_to: str | None = None
    created_at: datetime
    suggested_actions: list[SuggestedActionView] = Field(default_factory=list)
    approved_at: datetime | None = None
    rejected_at: datetime | None = None
    edited_body: str | None = None
    feedback_note: str | None = None
    feedback_action: str | None = None
    feedback_reason_code: str | None = None
    routing_category: str | None = None


class ThreadDetail(BaseModel):
    thread: ThreadSummary
    messages: list[MessageDetail]
    classification: ClassificationView | None = None
    draft: DraftView | None = None
    triage: TriageFlags | None = None
    audit_log: list[AuditEntry] = Field(default_factory=list)
