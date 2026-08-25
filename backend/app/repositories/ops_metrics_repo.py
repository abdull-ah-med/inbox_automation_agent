"""SQL aggregates for the weekly ops report.

All queries are mailbox-scoped. No analytics tables — reads from threads,
messages, drafts, audit_events, and sent_replies only.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.audit_event import AuditEvent
from app.models.db.draft import Draft
from app.models.db.message import Message
from app.models.db.sent_reply import SentReply
from app.models.db.thread import Thread
from app.models.schemas.audit_events import TriageAuditEvent
from app.models.schemas.email import EmailDirectionEnum, ThreadStateEnum
from app.models.schemas.ops_report import (
    CategoryCount,
    MailboxVolume,
    QueueSnapshot,
    RejectThemeCount,
)
from app.models.schemas.routing import REJECT_REASON_CODES

# Keep in sync with thread_repo awaiting / filtered views.
_AWAITING_STATES = (
    ThreadStateEnum.DRAFTED.value,
    ThreadStateEnum.REQUIRES_HUMAN.value,
    ThreadStateEnum.AWAITING_CLIENT.value,
    ThreadStateEnum.AWAITING_VENDOR.value,
    ThreadStateEnum.AWAITING_PARTNER.value,
)
_FILTERED_STATES = (
    ThreadStateEnum.SPAM.value,
    ThreadStateEnum.NO_ACTION.value,
)

_SPAM_EVENTS: tuple[str, ...] = (
    TriageAuditEvent.SPAM_DISCARDED,
    TriageAuditEvent.NO_ACTION_DISCARDED,
)

_INBOUND = EmailDirectionEnum.INBOUND.value


def _in_window(column: Any, date_from: datetime, date_to: datetime) -> Any:
    return (column >= date_from) & (column <= date_to)


async def volume_by_mailbox(
    session: AsyncSession,
    mailboxes: list[str],
    date_from: datetime,
    date_to: datetime,
) -> list[MailboxVolume]:
    """Threads with at least one inbound message whose ``received_at`` is in window."""
    if not mailboxes:
        return []

    stmt = (
        select(
            Thread.mailbox,
            func.count(func.distinct(Thread.id)).label("thread_volume"),
        )
        .select_from(Message)
        .join(Thread, Message.thread_id == Thread.id)
        .where(
            Thread.mailbox.in_(mailboxes),
            Message.direction == _INBOUND,
            _in_window(Message.received_at, date_from, date_to),
        )
        .group_by(Thread.mailbox)
    )
    result = await session.execute(stmt)
    by_email = {str(row.mailbox): int(row.thread_volume or 0) for row in result.all()}
    return [
        MailboxVolume(mailbox=email, email=email, thread_volume=by_email.get(email, 0))
        for email in mailboxes
    ]


async def count_spam_filtered(
    session: AsyncSession,
    mailboxes: list[str],
    date_from: datetime,
    date_to: datetime,
) -> int:
    """Distinct threads that entered SPAM or NO_ACTION in the window.

    Source: ``audit_events`` of type ``triage.spam_discarded`` /
    ``triage.no_action_discarded``, keyed by ``created_at``.
    """
    if not mailboxes:
        return 0

    distinct_threads = (
        select(AuditEvent.mailbox, AuditEvent.conversation_id)
        .where(
            AuditEvent.mailbox.in_(mailboxes),
            AuditEvent.event_type.in_(_SPAM_EVENTS),
            _in_window(AuditEvent.created_at, date_from, date_to),
        )
        .distinct()
        .subquery()
    )
    stmt = select(func.count()).select_from(distinct_threads)
    result = await session.execute(stmt)
    return int(result.scalar_one() or 0)


async def count_approvals_rejects(
    session: AsyncSession,
    mailboxes: list[str],
    date_from: datetime,
    date_to: datetime,
) -> tuple[int, int]:
    """Drafts whose ``approved_at`` or ``rejected_at`` falls in the window."""
    if not mailboxes:
        return 0, 0

    stmt = (
        select(
            func.count(case((_in_window(Draft.approved_at, date_from, date_to), 1))).label(
                "approvals"
            ),
            func.count(case((_in_window(Draft.rejected_at, date_from, date_to), 1))).label(
                "rejects"
            ),
        )
        .select_from(Draft)
        .join(Thread, Draft.thread_id == Thread.id)
        .where(Thread.mailbox.in_(mailboxes))
    )
    result = await session.execute(stmt)
    row = result.one()
    return int(row.approvals or 0), int(row.rejects or 0)


async def top_reject_themes(
    session: AsyncSession,
    mailboxes: list[str],
    date_from: datetime,
    date_to: datetime,
    *,
    limit: int = 5,
) -> list[RejectThemeCount]:
    """Rejects in window grouped by ``feedback_reason_code`` (unknown → other)."""
    if not mailboxes:
        return []

    reason = case(
        (
            Draft.feedback_reason_code.in_(REJECT_REASON_CODES),
            Draft.feedback_reason_code,
        ),
        else_="other",
    )
    stmt = (
        select(reason.label("reason_code"), func.count(Draft.id).label("count"))
        .select_from(Draft)
        .join(Thread, Draft.thread_id == Thread.id)
        .where(
            Thread.mailbox.in_(mailboxes),
            _in_window(Draft.rejected_at, date_from, date_to),
        )
        .group_by(reason)
        .order_by(func.count(Draft.id).desc(), reason.asc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return [
        RejectThemeCount(reason_code=str(row.reason_code), count=int(row.count or 0))
        for row in result.all()
    ]


async def avg_resolve_hours(
    session: AsyncSession,
    mailboxes: list[str],
    date_from: datetime,
    date_to: datetime,
) -> tuple[float | None, int]:
    """Mean hours from first inbound message to ``sent_replies.sent_at``.

    ``threads`` has no ``created_at``; resolve_start is ``min(messages.received_at)``
    for inbound messages on the thread. Rows with a null start or sent_at before
    start are excluded.
    """
    if not mailboxes:
        return None, 0

    first_inbound = (
        select(
            Message.thread_id,
            func.min(Message.received_at).label("resolve_start"),
        )
        .where(Message.direction == _INBOUND)
        .group_by(Message.thread_id)
        .subquery()
    )
    duration_hours = (
        func.extract("epoch", SentReply.sent_at - first_inbound.c.resolve_start) / 3600.0
    )
    stmt = (
        select(
            func.avg(duration_hours).label("avg_hours"),
            func.count(SentReply.id).label("sample_count"),
        )
        .select_from(SentReply)
        .join(Thread, SentReply.thread_id == Thread.id)
        .join(first_inbound, first_inbound.c.thread_id == SentReply.thread_id)
        .where(
            Thread.mailbox.in_(mailboxes),
            _in_window(SentReply.sent_at, date_from, date_to),
            SentReply.sent_at >= first_inbound.c.resolve_start,
        )
    )
    result = await session.execute(stmt)
    row = result.one()
    sample = int(row.sample_count or 0)
    if sample == 0 or row.avg_hours is None:
        return None, sample
    return float(row.avg_hours), sample


async def count_drafts_generated(
    session: AsyncSession,
    mailboxes: list[str],
    date_from: datetime,
    date_to: datetime,
) -> int:
    """Drafts whose ``created_at`` falls in the window."""
    if not mailboxes:
        return 0

    stmt = (
        select(func.count(Draft.id))
        .select_from(Draft)
        .join(Thread, Draft.thread_id == Thread.id)
        .where(
            Thread.mailbox.in_(mailboxes),
            _in_window(Draft.created_at, date_from, date_to),
        )
    )
    result = await session.execute(stmt)
    return int(result.scalar_one() or 0)


async def volume_by_category(
    session: AsyncSession,
    mailboxes: list[str],
    date_from: datetime,
    date_to: datetime,
) -> list[CategoryCount]:
    """Inbound threads in the window grouped by ``threads.category``."""
    if not mailboxes:
        return []

    category = func.coalesce(Thread.category, "uncategorized")
    stmt = (
        select(
            category.label("category"),
            func.count(func.distinct(Thread.id)).label("count"),
        )
        .select_from(Message)
        .join(Thread, Message.thread_id == Thread.id)
        .where(
            Thread.mailbox.in_(mailboxes),
            Message.direction == _INBOUND,
            _in_window(Message.received_at, date_from, date_to),
        )
        .group_by(category)
        .order_by(func.count(func.distinct(Thread.id)).desc(), category.asc())
    )
    result = await session.execute(stmt)
    return [
        CategoryCount(category=str(row.category), count=int(row.count or 0))
        for row in result.all()
    ]


async def queue_snapshot(
    session: AsyncSession,
    mailboxes: list[str],
    *,
    stale_after_hours: int = 24,
    now: datetime | None = None,
) -> tuple[QueueSnapshot, dict[str, tuple[int, int]]]:
    """Current open queue. Returns totals plus per-mailbox (awaiting, stale)."""
    empty = QueueSnapshot()
    if not mailboxes:
        return empty, {}

    cutoff = (now or datetime.now(UTC)) - timedelta(hours=stale_after_hours)
    stmt = (
        select(
            Thread.mailbox,
            func.count(case((Thread.state.in_(_AWAITING_STATES), 1))).label("awaiting"),
            func.count(
                case(
                    (
                        (Thread.last_message_at < cutoff) & (Thread.state.in_(_AWAITING_STATES)),
                        1,
                    )
                )
            ).label("stale"),
            func.count(case((Thread.state.in_(_FILTERED_STATES), 1))).label("filtered"),
            func.count(
                case(
                    (
                        Thread.state.in_(_AWAITING_STATES) & (Thread.urgency == "CRITICAL"),
                        1,
                    )
                )
            ).label("urgency_critical"),
            func.count(
                case(
                    (
                        Thread.state.in_(_AWAITING_STATES) & (Thread.urgency == "HIGH"),
                        1,
                    )
                )
            ).label("urgency_high"),
            func.count(
                case(
                    (
                        Thread.state.in_(_AWAITING_STATES) & (Thread.urgency == "NORMAL"),
                        1,
                    )
                )
            ).label("urgency_normal"),
            func.count(
                case(
                    (
                        Thread.state.in_(_AWAITING_STATES) & (Thread.urgency == "LOW"),
                        1,
                    )
                )
            ).label("urgency_low"),
        )
        .where(Thread.mailbox.in_(mailboxes))
        .group_by(Thread.mailbox)
    )
    result = await session.execute(stmt)
    by_mailbox: dict[str, tuple[int, int]] = {}
    awaiting = stale = filtered = 0
    critical = high = normal = low = 0
    for row in result.all():
        email = str(row.mailbox)
        awaiting_n = int(row.awaiting or 0)
        stale_n = int(row.stale or 0)
        by_mailbox[email] = (awaiting_n, stale_n)
        awaiting += awaiting_n
        stale += stale_n
        filtered += int(row.filtered or 0)
        critical += int(row.urgency_critical or 0)
        high += int(row.urgency_high or 0)
        normal += int(row.urgency_normal or 0)
        low += int(row.urgency_low or 0)
    return (
        QueueSnapshot(
            awaiting_action=awaiting,
            stale=stale,
            filtered=filtered,
            urgency_critical=critical,
            urgency_high=high,
            urgency_normal=normal,
            urgency_low=low,
        ),
        by_mailbox,
    )
