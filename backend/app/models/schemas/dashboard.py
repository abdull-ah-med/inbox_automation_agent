"""Dashboard and mailbox API response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.schemas.related import RelatedThreadItem

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
    draft_needed: bool | None = None
    needs_context: bool | None = None
    spam_reason: str | None = None
    context_reason: str | None = None
    action_items_summary: str | None = None
    routing_category: str | None = None
    is_internal: bool | None = None
    is_automated: bool | None = None
    outcome: str | None = None


class BadgeNowView(BaseModel):
    kind: str
    label: str


class TriageHistoryView(BaseModel):
    has_action_items: bool | None = None
    needs_context: bool | None = None
    is_spam: bool | None = None
    action_items_summary: str | None = None
    context_reason: str | None = None
    spam_reason: str | None = None


class ThreadPresentationView(BaseModel):
    """Derived Now story — single source for badges across header/cards/queue."""

    is_finished: bool
    open_work: bool
    in_needs_attention: bool
    urgency_assessed: str | None = None
    urgency_active: bool
    badges_now: list[BadgeNowView] = Field(default_factory=list)
    triage_history: TriageHistoryView = Field(default_factory=TriageHistoryView)
    suggest_resolve_default: bool = False
    show_resolution_banner: bool = False
    resolution_mode: Literal["auto", "manual"] | None = None
    disposition: str | None = None
    primary_badge: BadgeNowView | None = None
    resolution_reason: str | None = None
    resolution_summary: str | None = None


class ActivityEntryView(BaseModel):
    title: str
    body: str
    actor_kind: str
    event_type: str
    timestamp: datetime


class ThreadSummary(BaseModel):
    id: uuid.UUID
    mailbox: str
    mailbox_key: MailboxKey | str
    subject: str
    state: str
    urgency: str | None = None
    urgency_reason: str | None = None
    category: str | None = None
    last_message_at: datetime | None = None
    last_sender: str | None = None
    preview: str | None = None
    staleness_hours: float = 0.0
    message_count: int = 0
    has_draft: bool = False
    has_letter: bool = False
    teaching_note: str | None = None
    triage: TriageFlags | None = None
    outlook_url: str | None = None
    presentation: ThreadPresentationView | None = None


class MailboxOverview(BaseModel):
    mailbox: str
    email_address: str
    label: str = ""
    thread_count: int = 0
    unread_count: int = 0
    awaiting_action_count: int = 0
    filtered_count: int = 0
    stale_count: int = 0
    open_fyi_count: int = 0
    recently_resolved_draftassistant_count: int = 0
    urgency_breakdown: dict[str, int] = Field(default_factory=dict)
    recent_threads: list[ThreadSummary] = Field(default_factory=list)


class DashboardOverview(BaseModel):
    mailboxes: list[MailboxOverview]
    total_threads: int
    total_awaiting: int
    total_stale: int
    needs_attention: list[ThreadSummary] = Field(default_factory=list)
    open_fyi: list[ThreadSummary] = Field(default_factory=list)
    recently_resolved_by_draftassistant: list[ThreadSummary] = Field(default_factory=list)
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
    bcc: list[str] = Field(default_factory=list)
    body_text: str
    reply_text: str = ""
    body_preview: str | None = None
    received_at: datetime
    has_attachments: bool = False
    outlook_url: str | None = None
    meeting_message_type: str | None = None
    meeting_response_type: str | None = None
    sender_name: str | None = None
    sender_salute_name: str | None = None
    summary_one_line: str | None = None
    summary_ask: str | None = None
    summary_intent: str | None = None


class MessageHtmlBody(BaseModel):
    """On-demand Graph HTML for Outlook View (not stored)."""

    content_type: Literal["html", "text"]
    html: str


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


class AppliedSkillView(BaseModel):
    id: uuid.UUID
    name: str


class DraftToolCallView(BaseModel):
    skill_id: str
    path: str
    is_error: bool = False


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
    approval_note: str | None = None
    approval_scope: str | None = None
    applied_skills: list[AppliedSkillView] = Field(default_factory=list)
    tool_calls: list[DraftToolCallView] | None = None


class SentReplyView(BaseModel):
    id: uuid.UUID
    thread_id: uuid.UUID
    message_id: uuid.UUID
    draft_id: uuid.UUID | None = None
    sent_body_snapshot: str
    sent_at: datetime
    matched_by: Literal["approved_draft", "time_window", "manual"]
    created_at: datetime | None = None


class DraftVsSentDiff(BaseModel):
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)


class ThreadHeader(BaseModel):
    subject: str
    mailbox: str


class ReplyAddresseeView(BaseModel):
    email: str
    salute_name: str
    source: str
    source_kind: str
    directory_hit: bool = False


class ThreadDetail(BaseModel):
    thread: ThreadSummary
    messages: list[MessageDetail]
    classification: ClassificationView | None = None
    draft: DraftView | None = None
    triage: TriageFlags | None = None
    audit_log: list[AuditEntry] = Field(default_factory=list)
    activity: list[ActivityEntryView] = Field(default_factory=list)
    sent_reply: SentReplyView | None = None
    draft_vs_sent_diff: DraftVsSentDiff | None = None
    associated_threads: list[RelatedThreadItem] = Field(default_factory=list)
    reply_addressee: ReplyAddresseeView | None = None
