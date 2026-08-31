"""Date-range ops metrics for the weekly leadership report.

FORMULAS (v1) — the PDF renderer must call ``get_metrics``; it must not
re-query with different logic.

Window
    Inclusive timestamps stored in UTC. Query ``from``/``to`` are inclusive
    calendar days in ``ops_report_timezone`` (default America/New_York):
    start of ``from`` through end of ``to``. Default preview is the last 7
    New York calendar days including today. The weekly job uses last
    completed Mon-Sun in that timezone. Max span: 93 days.

Volume by mailbox
    Count of threads that received at least one inbound message in the window
    (``messages.received_at`` where ``direction = inbound``). ``threads`` has
    no ``created_at``; inbound ``received_at`` is the volume clock. Broken
    down per mailbox email; configured mailboxes with zero inbound still
    appear. ``total_volume`` is the sum.

Spam filtered
    Count of distinct (mailbox, conversation_id) pairs with an audit event
    ``triage.spam_discarded`` or ``triage.no_action_discarded`` whose
    ``created_at`` falls in the window.

Approval rate
    ``approvals / (approvals + rejects)`` for drafts whose ``approved_at`` or
    ``rejected_at`` falls in the window. Rate is 0.0 when the denominator is
    0. Returned as a 0-1 float.

Top reject themes
    Rejects in the window grouped by ``feedback_reason_code`` (closed set:
    tone, factual, wrong_action, incomplete, policy, recipients, other).
    Null or unknown codes map to ``other``. Top 5 by count.

Avg time to resolve
    For ``sent_replies`` whose ``sent_at`` is in the window:
    ``sent_at - resolve_start`` in hours. resolve_start (v1) is the first
    inbound ``messages.received_at`` on the thread (threads have no
    ``created_at``). Null starts and negative durations are excluded.
    ``avg_resolve_hours`` is None when ``resolve_sample_count`` is 0.

Drafts generated
    Count of drafts whose ``created_at`` falls in the window.

Volume by category
    Distinct inbound threads in the window grouped by ``threads.category``
    (null → uncategorized).

Queue snapshot
    Current open work, not limited to the period: awaiting-action states,
    stale (awaiting and last_message_at older than ``staleness_threshold_hours``),
    filtered (SPAM / NO_ACTION), and urgency counts on open threads.
    Per-mailbox awaiting/stale are merged onto ``volume_by_mailbox``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import InvalidDateRangeError
from app.core.mailbox_keys import infer_mailbox_key
from app.core.mailbox_keys import scoped_mailboxes as scope_allowed_mailboxes
from app.models.schemas.ops_report import (
    MailboxVolume,
    OpsMetricsResponse,
    OpsPeriod,
)
from app.repositories import ops_metrics_repo

MAX_WINDOW_DAYS = 93
DEFAULT_ROLLING_DAYS = 7
TOP_REJECT_THEMES = 5


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _calendar_date(value: datetime) -> date:
    """Wall-clock date as given. Timezone does not shift the day number."""
    return value.date()


def inclusive_calendar_range(
    date_from: datetime,
    date_to: datetime,
    timezone_name: str = "America/New_York",
) -> tuple[datetime, datetime]:
    """Inclusive start-of-from through end-of-to in ``timezone_name``, as UTC."""
    tz = ZoneInfo(timezone_name)
    start_day = _calendar_date(date_from)
    end_day = _calendar_date(date_to)
    start = datetime(start_day.year, start_day.month, start_day.day, tzinfo=tz)
    end = datetime(end_day.year, end_day.month, end_day.day, 23, 59, 59, 999999, tzinfo=tz)
    return validate_window(start, end)


def rolling_days(
    days: int = DEFAULT_ROLLING_DAYS,
    *,
    timezone_name: str = "America/New_York",
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Last ``days`` inclusive calendar days in ``timezone_name``, as UTC."""
    tz = ZoneInfo(timezone_name)
    today = ensure_utc(now or datetime.now(UTC)).astimezone(tz).date()
    start_day = today - timedelta(days=days - 1)
    return inclusive_calendar_range(
        datetime(start_day.year, start_day.month, start_day.day, tzinfo=tz),
        datetime(today.year, today.month, today.day, tzinfo=tz),
        timezone_name,
    )


def resolve_query_window(
    date_from: datetime | None,
    date_to: datetime | None,
    timezone_name: str = "America/New_York",
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    if date_from is None and date_to is None:
        return rolling_days(timezone_name=timezone_name, now=now)
    if date_from is None or date_to is None:
        raise InvalidDateRangeError("both from and to are required when either is set")
    return inclusive_calendar_range(date_from, date_to, timezone_name)


def last_calendar_week(
    timezone_name: str = "America/New_York",
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Last completed Monday-Sunday in ``timezone_name``, returned as UTC.

    On a Monday this is the previous calendar week, which is what the weekly
    Monday job should print.
    """
    tz = ZoneInfo(timezone_name)
    current = ensure_utc(now or datetime.now(UTC)).astimezone(tz)
    this_monday = current.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days=current.weekday()
    )
    last_monday = this_monday - timedelta(days=7)
    last_sunday_end = this_monday - timedelta(microseconds=1)
    return last_monday.astimezone(UTC), last_sunday_end.astimezone(UTC)


def validate_window(date_from: datetime, date_to: datetime) -> tuple[datetime, datetime]:
    start = ensure_utc(date_from)
    end = ensure_utc(date_to)
    if start > end:
        raise InvalidDateRangeError("date from must be on or before date to")
    if end - start > timedelta(days=MAX_WINDOW_DAYS):
        raise InvalidDateRangeError(f"date range exceeds {MAX_WINDOW_DAYS} days")
    return start, end


def scoped_mailboxes(settings: Settings, mailbox: str | None) -> list[str]:
    """Restrict to ``settings.mailbox_list``; optional single-mailbox filter."""
    return scope_allowed_mailboxes(settings, mailbox)


async def get_metrics(
    session: AsyncSession,
    settings: Settings,
    date_from: datetime,
    date_to: datetime,
    *,
    mailbox: str | None = None,
) -> OpsMetricsResponse:
    start, end = validate_window(date_from, date_to)
    mailboxes = scoped_mailboxes(settings, mailbox)

    volume_rows = await ops_metrics_repo.volume_by_mailbox(session, mailboxes, start, end)
    queue, queue_by_mailbox = await ops_metrics_repo.queue_snapshot(
        session,
        mailboxes,
        stale_after_hours=settings.staleness_threshold_hours,
    )
    volume_by_mailbox = [
        MailboxVolume(
            mailbox=str(infer_mailbox_key(row.email)),
            email=row.email,
            thread_volume=row.thread_volume,
            awaiting_action=queue_by_mailbox.get(row.email, (0, 0))[0],
            stale=queue_by_mailbox.get(row.email, (0, 0))[1],
        )
        for row in volume_rows
    ]
    approvals, rejects = await ops_metrics_repo.count_approvals_rejects(
        session, mailboxes, start, end
    )
    denom = approvals + rejects
    rate = (approvals / denom) if denom else 0.0
    avg_hours, sample = await ops_metrics_repo.avg_resolve_hours(session, mailboxes, start, end)
    return OpsMetricsResponse(
        period=OpsPeriod(date_from=start, date_to=end),
        volume_by_mailbox=volume_by_mailbox,
        total_volume=sum(row.thread_volume for row in volume_by_mailbox),
        spam_filtered=await ops_metrics_repo.count_spam_filtered(session, mailboxes, start, end),
        drafts_generated=await ops_metrics_repo.count_drafts_generated(
            session, mailboxes, start, end
        ),
        approvals=approvals,
        rejects=rejects,
        approval_rate=rate,
        top_reject_themes=await ops_metrics_repo.top_reject_themes(
            session,
            mailboxes,
            start,
            end,
            limit=TOP_REJECT_THEMES,
        ),
        volume_by_category=await ops_metrics_repo.volume_by_category(
            session, mailboxes, start, end
        ),
        avg_resolve_hours=avg_hours,
        resolve_sample_count=sample,
        queue=queue,
        generated_at=datetime.now(UTC),
    )
