"""SQL predicates for derived thread dispositions (keeps thread_repo under LOC budget)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import aliased

from app.models.db.audit_event import AuditEvent
from app.models.db.draft import Draft
from app.models.db.message import Message
from app.models.db.thread import Thread
from app.models.schemas.audit_events import TriageAuditEvent
from app.models.schemas.email import ThreadStateEnum

RECENTLY_RESOLVED_DRAFTASSISTANT_HOURS = 48

_AutomatedMessage = aliased(Message)

_TRIAGE_EVENT_TYPES = (
    TriageAuditEvent.ACTION_NEEDED.value,
    TriageAuditEvent.NO_ACTION_DISCARDED.value,
    TriageAuditEvent.SPAM_DISCARDED.value,
    TriageAuditEvent.FAILED.value,
)


def latest_draft_ranked() -> Any:
    return (
        select(
            Draft.thread_id.label("tid"),
            Draft.feedback_action.label("feedback_action"),
            Draft.approved_at.label("approved_at"),
            Draft.body.label("body"),
            func.row_number()
            .over(
                partition_by=Draft.thread_id,
                order_by=(Draft.created_at.desc(), Draft.id.desc()),
            )
            .label("rn"),
        )
    ).subquery()


def draft_unanswered(latest_draft: Any) -> Any:
    return and_(
        latest_draft.c.approved_at.is_(None),
        or_(
            latest_draft.c.feedback_action.is_(None),
            latest_draft.c.feedback_action != "wrong",
        ),
    )


def has_letter_sql(latest_draft: Any) -> Any:
    return and_(
        latest_draft.c.tid.is_not(None),
        func.length(func.btrim(func.coalesce(latest_draft.c.body, ""))) > 0,
    )


def no_letter_sql(latest_draft: Any) -> Any:
    return or_(
        latest_draft.c.tid.is_(None),
        func.length(func.btrim(func.coalesce(latest_draft.c.body, ""))) == 0,
    )


def is_operational_alert() -> Any:
    return or_(
        Thread.alert_fingerprint.is_not(None),
        exists(
            select(1).where(
                _AutomatedMessage.thread_id == Thread.id,
                _AutomatedMessage.is_automated.is_(True),
            )
        ),
    )


def triage_informational() -> Any:
    """Latest triage for this thread says no action items (informational / FYI)."""
    newer = aliased(AuditEvent)
    return exists(
        select(1)
        .select_from(AuditEvent)
        .where(
            AuditEvent.conversation_id == Thread.conversation_id,
            AuditEvent.mailbox == Thread.mailbox,
            AuditEvent.event_type.in_(_TRIAGE_EVENT_TYPES),
            or_(
                AuditEvent.event_type == TriageAuditEvent.NO_ACTION_DISCARDED.value,
                AuditEvent.payload["has_action_items"].as_boolean().is_(False),
            ),
            ~exists(
                select(1)
                .select_from(newer)
                .where(
                    newer.conversation_id == AuditEvent.conversation_id,
                    newer.mailbox == AuditEvent.mailbox,
                    newer.event_type.in_(_TRIAGE_EVENT_TYPES),
                    newer.created_at > AuditEvent.created_at,
                )
            ),
        )
    )


def is_actionable_operational_alert() -> Any:
    """Ops automated alert that still needs Elise — not domain-auth / FYI triage."""
    return and_(is_operational_alert(), ~triage_informational())


def is_reply_review(latest_draft: Any) -> Any:
    """Disposition reply_review: DRAFTED with non-empty letter still awaiting Elise."""
    return and_(
        Thread.state == ThreadStateEnum.DRAFTED.value,
        has_letter_sql(latest_draft),
        draft_unanswered(latest_draft),
    )


def needs_elise_action(latest_draft: Any) -> Any:
    """SQL: Needs Attention dispositions — letter review, offline action, or pipeline failure."""
    unanswered = draft_unanswered(latest_draft)
    no_draft = latest_draft.c.tid.is_(None)
    requires_human = and_(
        Thread.state == ThreadStateEnum.REQUIRES_HUMAN.value,
        or_(no_draft, unanswered),
    )
    action_no_draft = and_(
        Thread.state == ThreadStateEnum.DRAFTED.value,
        no_letter_sql(latest_draft),
        or_(no_draft, unanswered),
        is_actionable_operational_alert(),
    )
    return or_(requires_human, is_reply_review(latest_draft), action_no_draft)


def is_fyi_briefing(latest_draft: Any) -> Any:
    unanswered = draft_unanswered(latest_draft)
    no_draft = latest_draft.c.tid.is_(None)
    return and_(
        Thread.state == ThreadStateEnum.DRAFTED.value,
        no_letter_sql(latest_draft),
        or_(no_draft, unanswered),
        or_(~is_operational_alert(), triage_informational()),
    )


def draftassistant_resolved_recently(now: datetime, hours: int = RECENTLY_RESOLVED_DRAFTASSISTANT_HOURS) -> Any:
    cutoff = now - timedelta(hours=hours)
    reviewer = exists(
        select(1).where(
            AuditEvent.conversation_id == Thread.conversation_id,
            AuditEvent.mailbox == Thread.mailbox,
            AuditEvent.event_type == "thread.resolved.reviewer",
        )
    )
    return and_(
        Thread.state.in_(
            {ThreadStateEnum.RESOLVED.value, ThreadStateEnum.NO_ACTION.value},
        ),
        Thread.last_updated_at >= cutoff,
        ~reviewer,
    )
