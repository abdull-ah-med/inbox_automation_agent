"""Thread repository — async CRUD returning Pydantic schemas."""

from __future__ import annotations

import base64
import inspect
import json
import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Select, and_, case, exists, func, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.exceptions import InvalidCursorError
from app.core.mailbox_keys import infer_mailbox_key
from app.core.outlook_links import outlook_web_link
from app.core.tenant_scope import TenantScope
from app.core.thread_policy import presentation_from_flags
from app.models.db.audit_event import AuditEvent
from app.models.db.draft import Draft
from app.models.db.message import Message
from app.models.db.thread import Thread
from app.models.schemas.dashboard import (
    BadgeNowView,
    ThreadPresentationView,
    ThreadSummary,
    TriageFlags,
    TriageHistoryView,
)
from app.models.schemas.audit_events import TriageAuditEvent
from app.models.schemas.email import ThreadStateEnum
from app.repositories import audit_repo, draft_repo, message_repo
from app.repositories.thread_list_helpers import (
    card_preview,
    display_state,
    enriched_triage,
    party_sender,
)

#: Real actionable states — a message reached the pipeline and either has a
#: draft awaiting review or needs a human because generation failed. ``NEW``
#: (not yet triaged) and the terminal filtered outcomes (``SPAM`` /
#: ``NO_ACTION``) are deliberately excluded: they are not "awaiting Elise's
#: action".
_AWAITING_STATES = (
    ThreadStateEnum.DRAFTED.value,
    ThreadStateEnum.REQUIRES_HUMAN.value,
    ThreadStateEnum.AWAITING_CLIENT.value,
    ThreadStateEnum.AWAITING_VENDOR.value,
    ThreadStateEnum.AWAITING_PARTNER.value,
)

#: Dashboard Needs Attention — Elise still must act. ``AWAITING_*`` means
#: waiting on someone else. Finished in-app review (approved or no-reply
#: ``wrong``) drops even while ``threads.state`` is still ``DRAFTED``.
_NEEDS_ATTENTION_STATES = (
    ThreadStateEnum.DRAFTED.value,
    ThreadStateEnum.REQUIRES_HUMAN.value,
)

#: Terminal triage outcomes that carry no action for Elise — counted on the
#: dashboard as ``filtered_count``; mailbox list shows them by default.
_FILTERED_STATES = (ThreadStateEnum.SPAM.value,)

#: Mailbox list filter aliases — not stored ``threads.state`` values.
#: ``AWAITING_ACTION`` / ``STALE`` match dashboard ``awaiting_action_count`` /
#: ``stale_count``. ``FILTERED`` matches ``filtered_count`` (SPAM only).
#: ``REPLY_REVIEW`` / ``OPEN_FYI`` match disposition queues (not raw DRAFTED).
_AWAITING_ACTION_FILTER = "AWAITING_ACTION"
_STALE_FILTER = "STALE"
_FILTERED_FILTER = "FILTERED"
_REPLY_REVIEW_FILTER = "REPLY_REVIEW"
_OPEN_FYI_FILTER = "OPEN_FYI"
_RECENTLY_RESOLVED_DRAFTASSISTANT_FILTER = "RECENTLY_RESOLVED_DRAFTASSISTANT"
_RECENTLY_RESOLVED_DRAFTASSISTANT_HOURS = 48

_AutomatedMessage = aliased(Message)

# Mailbox date filters use US Eastern calendar days (matches ops reports).
_MAILBOX_LIST_TZ = ZoneInfo("America/New_York")


def _to_presentation_view(
    *,
    state: str,
    urgency: str | None,
    triage: TriageFlags | None,
    category: str | None,
    draft_review_finished: bool = False,
    closing_signal: bool = False,
    resolution_reason_corrected: bool = False,
    has_letter: bool = False,
    has_teaching_note: bool = False,
    resolved_by: str | None = None,
    resolution_reason: str | None = None,
    resolution_summary: str | None = None,
    forced_disposition: str | None = None,
) -> ThreadPresentationView:
    derived = presentation_from_flags(
        state=state,
        urgency=urgency,
        triage=triage,
        draft_review_finished=draft_review_finished,
        closing_signal=closing_signal,
        category=category,
        resolution_reason_corrected=resolution_reason_corrected,
        has_letter=has_letter,
        has_teaching_note=has_teaching_note,
        resolved_by=resolved_by,
        resolution_reason=resolution_reason,
        resolution_summary=resolution_summary,
        forced_disposition=forced_disposition,
    )
    return ThreadPresentationView(
        is_finished=derived.is_finished,
        open_work=derived.open_work,
        in_needs_attention=derived.in_needs_attention,
        urgency_assessed=derived.urgency_assessed,
        urgency_active=derived.urgency_active,
        badges_now=[
            BadgeNowView(kind=badge.kind.value, label=badge.label) for badge in derived.badges_now
        ],
        triage_history=TriageHistoryView(
            has_action_items=derived.triage_history.has_action_items,
            needs_context=derived.triage_history.needs_context,
            is_spam=derived.triage_history.is_spam,
            action_items_summary=derived.triage_history.action_items_summary,
            context_reason=derived.triage_history.context_reason,
            spam_reason=derived.triage_history.spam_reason,
        ),
        suggest_resolve_default=derived.suggest_resolve_default,
        show_resolution_banner=derived.show_resolution_banner,
        resolution_mode=derived.resolution_mode,
        disposition=derived.disposition.value if derived.disposition is not None else None,
        primary_badge=(
            BadgeNowView(kind=derived.primary_badge.kind.value, label=derived.primary_badge.label)
            if derived.primary_badge is not None
            else None
        ),
        resolution_reason=derived.resolution_reason,
        resolution_summary=derived.resolution_summary,
    )


def with_presentation(
    summary: ThreadSummary,
    *,
    draft_review_finished: bool = False,
    closing_signal: bool = False,
    resolution_reason_corrected: bool = False,
    resolved_by: str | None = None,
    resolution_reason: str | None = None,
    resolution_summary: str | None = None,
    forced_disposition: str | None = None,
) -> ThreadSummary:
    return summary.model_copy(
        update={
            "presentation": _to_presentation_view(
                state=summary.state,
                urgency=summary.urgency,
                triage=summary.triage,
                category=summary.category,
                draft_review_finished=draft_review_finished,
                closing_signal=closing_signal,
                resolution_reason_corrected=resolution_reason_corrected,
                has_letter=summary.has_letter,
                has_teaching_note=bool(summary.teaching_note),
                resolved_by=resolved_by,
                resolution_reason=resolution_reason,
                resolution_summary=resolution_summary,
                forced_disposition=forced_disposition,
            )
        }
    )


def _latest_message_ranked() -> Any:
    return (
        select(
            Message.thread_id.label("tid"),
            Message.sender.label("sender"),
            Message.direction.label("direction"),
            Message.to_recipients.label("to_recipients"),
            Message.body_preview.label("body_preview"),
            Message.body_text.label("body_text"),
            Message.unique_body_text.label("unique_body_text"),
            Message.graph_message_id.label("graph_message_id"),
            func.row_number()
            .over(
                partition_by=Message.thread_id,
                order_by=Message.received_at.desc(),
            )
            .label("rn"),
        )
    ).subquery()


class ThreadSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    mailbox: str
    conversation_id: str
    subject: str
    state: str
    urgency: str | None = None
    urgency_reason: str | None = None
    category: str | None = None
    last_message_at: datetime | None = None
    last_updated_at: datetime
    alert_fingerprint: str | None = None
    alert_signature: str | None = None
    alert_sender_norm: str | None = None


async def get_by_conversation_id(
    session: AsyncSession,
    conversation_id: str,
    *,
    mailbox: str,
) -> ThreadSchema | None:
    """Look up a thread by mailbox + conversation_id (unique per mailbox)."""
    stmt = select(Thread).where(
        Thread.mailbox == mailbox,
        Thread.conversation_id == conversation_id,
    )
    result = await session.execute(stmt)
    thread = result.scalar_one_or_none()
    if thread is None:
        return None
    return ThreadSchema.model_validate(thread)


async def find_thread_by_conversation_id(
    session: AsyncSession,
    conversation_id: str,
    *,
    mailboxes: list[str],
) -> ThreadSchema | None:
    """Find a thread with this Graph conversation id in any of ``mailboxes``."""
    if not conversation_id or not mailboxes:
        return None
    stmt = (
        select(Thread)
        .where(
            Thread.conversation_id == conversation_id,
            Thread.mailbox.in_(mailboxes),
        )
        .order_by(Thread.last_message_at.desc().nullslast())
        .limit(1)
    )
    result = await session.execute(stmt)
    thread = result.scalar_one_or_none()
    if thread is None:
        return None
    return ThreadSchema.model_validate(thread)


async def get_by_id(
    session: AsyncSession,
    thread_id: uuid.UUID,
    scope: TenantScope,
) -> ThreadSchema | None:
    stmt = select(Thread).where(
        Thread.id == thread_id,
        Thread.mailbox.in_(list(scope.mailboxes)),
    )
    result = await session.execute(stmt)
    thread = result.scalar_one_or_none()
    if thread is None:
        return None
    return ThreadSchema.model_validate(thread)


async def get_by_id_trusted(
    session: AsyncSession,
    thread_id: uuid.UUID,
) -> ThreadSchema | None:
    """Load a thread by id, then pin tenant scope to that row's mailbox.

    For trusted internal thread_ids (pipeline, HITL) that already came from
    our database. HTTP handlers must keep using ``get_by_id`` with an explicit
    ``TenantScope``.
    """
    mailbox = (
        await session.execute(select(Thread.mailbox).where(Thread.id == thread_id))
    ).scalar_one_or_none()
    if inspect.isawaitable(mailbox):
        mailbox = await mailbox
    if mailbox is None:
        return None
    if not isinstance(mailbox, str) or not mailbox.strip():
        # Unit tests mock ``get_by_id`` and pass a non-DB session.
        return await get_by_id(session, thread_id, TenantScope.single("internal"))
    return await get_by_id(session, thread_id, TenantScope.single(mailbox))


async def list_by_ids(
    session: AsyncSession,
    thread_ids: Sequence[uuid.UUID],
) -> dict[uuid.UUID, ThreadSchema]:
    """Load many threads in one query, keyed by id."""
    if not thread_ids:
        return {}
    stmt = select(Thread).where(Thread.id.in_(list(thread_ids)))
    result = await session.execute(stmt)
    return {row.id: ThreadSchema.model_validate(row) for row in result.scalars().all()}


async def list_by_mailbox_conversations(
    session: AsyncSession,
    pairs: Sequence[tuple[str, str]],
) -> dict[tuple[str, str], ThreadSchema]:
    """Load threads keyed by ``(mailbox, conversation_id)`` in one query."""
    if not pairs:
        return {}
    stmt = select(Thread).where(tuple_(Thread.mailbox, Thread.conversation_id).in_(list(pairs)))
    result = await session.execute(stmt)
    threads = result.scalars().all()
    return {(row.mailbox, row.conversation_id): ThreadSchema.model_validate(row) for row in threads}


_APOSTROPHE_FROM = "'\u2018\u2019`"


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _folded_text(column: object) -> Any:
    return func.translate(column, _APOSTROPHE_FROM, "")


async def search_keyword_threads(
    session: AsyncSession,
    *,
    tokens: Sequence[str],
    mailboxes: Sequence[str],
    top_k: int,
) -> list[ThreadSchema]:
    """Find threads by folded subject/sender/preview. No embeddings required."""
    cleaned = [token.strip() for token in tokens if token.strip()]
    if not cleaned or not mailboxes or top_k < 1:
        return []
    token_filters = []
    for token in cleaned:
        pattern = f"%{_escape_like(token)}%"
        token_filters.append(
            or_(
                _folded_text(Thread.subject).ilike(pattern, escape="\\"),
                exists(
                    select(1).where(
                        Message.thread_id == Thread.id,
                        or_(
                            _folded_text(Message.sender).ilike(pattern, escape="\\"),
                            _folded_text(func.coalesce(Message.body_preview, "")).ilike(
                                pattern, escape="\\"
                            ),
                        ),
                    )
                ),
            )
        )
    stmt = (
        select(Thread)
        .where(Thread.mailbox.in_(list(mailboxes)), and_(*token_filters))
        .order_by(Thread.last_message_at.desc().nullslast())
        .limit(top_k)
    )
    result = await session.execute(stmt)
    return [ThreadSchema.model_validate(row) for row in result.scalars().all()]


async def upsert_thread(
    session: AsyncSession,
    *,
    mailbox: str,
    conversation_id: str,
    subject: str,
    last_message_at: datetime | None = None,
    state: str = ThreadStateEnum.NEW.value,
) -> ThreadSchema:
    """Insert a thread or update subject/last_message_at for mailbox+conversation_id.

    Uses Postgres ``ON CONFLICT`` so concurrent webhook+poll writers do not race.
    """
    insert_stmt = insert(Thread).values(
        mailbox=mailbox,
        conversation_id=conversation_id,
        subject=subject,
        state=state,
        last_message_at=last_message_at,
    )
    upsert_stmt = insert_stmt.on_conflict_do_update(
        constraint="uq_threads_mailbox_conversation",
        set_={
            "subject": subject,
            "last_message_at": func.greatest(Thread.last_message_at, last_message_at),
        },
    ).returning(Thread)
    result = await session.execute(upsert_stmt)
    thread = result.scalar_one()
    await session.flush()
    return ThreadSchema.model_validate(thread)


async def set_thread_outcome(
    session: AsyncSession,
    thread_id: uuid.UUID,
    *,
    state: str,
    urgency: str | None = None,
    urgency_reason: str | None = None,
) -> ThreadSchema | None:
    """Persist the pipeline's latest state (and optional urgency) for a thread.

    Called by ``pipeline_service`` after triage/draft outcomes are decided so
    ``threads.state``/``threads.urgency`` reflect what actually happened
    instead of staying at the ``NEW`` default forever. ``urgency`` is left
    untouched when ``None`` (e.g. spam/no-action outcomes carry no urgency).
    """
    values: dict[str, object] = {"state": state}
    if urgency is not None:
        values["urgency"] = urgency
    if urgency_reason is not None:
        values["urgency_reason"] = urgency_reason
    stmt = update(Thread).where(Thread.id == thread_id).values(**values).returning(Thread)
    result = await session.execute(stmt)
    thread = result.scalar_one_or_none()
    if thread is None:
        return None
    await session.flush()
    return ThreadSchema.model_validate(thread)


async def set_urgency(
    session: AsyncSession,
    thread_id: uuid.UUID,
    *,
    urgency: str,
    urgency_reason: str,
) -> ThreadSchema | None:
    """Update thread urgency + reason without changing state."""
    stmt = (
        update(Thread)
        .where(Thread.id == thread_id)
        .values(urgency=urgency, urgency_reason=urgency_reason)
        .returning(Thread)
    )
    result = await session.execute(stmt)
    thread = result.scalar_one_or_none()
    if thread is None:
        return None
    await session.flush()
    return ThreadSchema.model_validate(thread)


_FINISHED_STATES = frozenset(
    {
        ThreadStateEnum.RESOLVED.value,
        ThreadStateEnum.NO_ACTION.value,
        ThreadStateEnum.SPAM.value,
    }
)
_ALERT_ASSOCIATION_LIMIT = 25


async def set_alert_fingerprint(
    session: AsyncSession,
    thread_id: uuid.UUID,
    fingerprint: str | None,
    *,
    signature: str | None = None,
    sender_norm: str | None = None,
) -> ThreadSchema | None:
    stmt = (
        update(Thread)
        .where(Thread.id == thread_id)
        .values(
            alert_fingerprint=fingerprint,
            alert_signature=signature,
            alert_sender_norm=sender_norm,
        )
        .returning(Thread)
    )
    result = await session.execute(stmt)
    thread = result.scalar_one_or_none()
    if thread is None:
        return None
    await session.flush()
    return ThreadSchema.model_validate(thread)


def _alert_cluster_filter(
    *,
    mailbox: str,
    fingerprint: str,
    signature: str | None,
    sender_norm: str | None,
    cutoff: datetime,
    open_only: bool,
) -> Any:
    match = Thread.alert_fingerprint == fingerprint
    if signature and sender_norm:
        match = or_(
            match,
            and_(
                Thread.alert_sender_norm == sender_norm,
                Thread.alert_signature == signature,
            ),
        )
    clauses = [
        Thread.mailbox == mailbox,
        Thread.alert_fingerprint.is_not(None),
        Thread.last_message_at >= cutoff,
        match,
    ]
    if open_only:
        clauses.append(Thread.state.notin_(_FINISHED_STATES))
    return and_(*clauses)


async def list_open_alert_cluster(
    session: AsyncSession,
    *,
    mailbox: str,
    fingerprint: str,
    now: datetime,
    signature: str | None = None,
    sender_norm: str | None = None,
    window_hours: int = 48,
    for_update: bool = False,
) -> list[ThreadSchema]:
    cutoff = now - timedelta(hours=window_hours)
    stmt = (
        select(Thread)
        .where(
            _alert_cluster_filter(
                mailbox=mailbox,
                fingerprint=fingerprint,
                signature=signature,
                sender_norm=sender_norm,
                cutoff=cutoff,
                open_only=True,
            )
        )
        .order_by(Thread.id.asc())
    )
    if for_update:
        stmt = stmt.with_for_update()
    result = await session.execute(stmt)
    return [ThreadSchema.model_validate(row) for row in result.scalars().all()]


async def list_alert_association_candidates(
    session: AsyncSession,
    *,
    mailbox: str,
    fingerprint: str,
    now: datetime,
    signature: str | None = None,
    sender_norm: str | None = None,
    window_days: int = 90,
    limit: int = _ALERT_ASSOCIATION_LIMIT,
) -> list[ThreadSchema]:
    cutoff = now - timedelta(days=window_days)
    stmt = (
        select(Thread)
        .where(
            _alert_cluster_filter(
                mailbox=mailbox,
                fingerprint=fingerprint,
                signature=signature,
                sender_norm=sender_norm,
                cutoff=cutoff,
                open_only=False,
            )
        )
        .order_by(Thread.last_message_at.desc().nullslast(), Thread.id.asc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return [ThreadSchema.model_validate(row) for row in result.scalars().all()]


_DRIP_WINDOW_SCAN_LIMIT = 100


async def list_mailbox_threads_in_window(
    session: AsyncSession,
    *,
    mailbox: str,
    now: datetime,
    window_days: int = 90,
    limit: int = _DRIP_WINDOW_SCAN_LIMIT,
) -> list[ThreadSchema]:
    """Recent threads in one mailbox for drip association filtering in the service."""
    cutoff = now - timedelta(days=window_days)
    stmt = (
        select(Thread)
        .where(
            Thread.mailbox == mailbox,
            Thread.last_message_at.is_not(None),
            Thread.last_message_at >= cutoff,
        )
        .order_by(Thread.last_message_at.desc().nullslast(), Thread.id.asc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return [ThreadSchema.model_validate(row) for row in result.scalars().all()]


def _encode_cursor(last_message_at: datetime | None, thread_id: uuid.UUID) -> str:
    payload = {
        "t": last_message_at.isoformat() if last_message_at else None,
        "id": str(thread_id),
    }
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime | None, uuid.UUID]:
    try:
        raw = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        ts = datetime.fromisoformat(raw["t"]) if raw.get("t") else None
        return ts, uuid.UUID(raw["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise InvalidCursorError("Invalid cursor") from exc


def _staleness_hours(last_message_at: datetime | None, now: datetime) -> float:
    if last_message_at is None:
        return 0.0
    aware = last_message_at if last_message_at.tzinfo else last_message_at.replace(tzinfo=UTC)
    return max(0.0, (now - aware).total_seconds() / 3600.0)


def _mailbox_inclusive_day_bounds(
    day_from: date | None,
    day_to: date | None,
) -> tuple[datetime | None, datetime | None]:
    """Inclusive US Eastern calendar days on ``last_message_at``."""
    start: datetime | None = None
    end_exclusive: datetime | None = None
    if day_from is not None:
        start = datetime(
            day_from.year,
            day_from.month,
            day_from.day,
            tzinfo=_MAILBOX_LIST_TZ,
        )
    if day_to is not None:
        next_day = day_to + timedelta(days=1)
        end_exclusive = datetime(
            next_day.year,
            next_day.month,
            next_day.day,
            tzinfo=_MAILBOX_LIST_TZ,
        )
    return start, end_exclusive


def _apply_mailbox_list_filters(
    stmt: Select[
        tuple[Thread, int, str | None, str | None, list[str] | None, str | None, str | None]
    ],
    *,
    state: str | None,
    urgency: str | None,
    stale_only: bool,
    stale_after_hours: int,
    date_from: date | None,
    date_to: date | None,
    cursor: str | None,
    now: datetime,
) -> Select[tuple[Thread, int, str | None, str | None, list[str] | None, str | None, str | None]]:
    if state in {_AWAITING_ACTION_FILTER, _STALE_FILTER}:
        latest_draft = _latest_draft_ranked()
        stmt = stmt.outerjoin(
            latest_draft,
            (latest_draft.c.tid == Thread.id) & (latest_draft.c.rn == 1),
        )
        needs_elise = _needs_elise_action(latest_draft)
        if state == _STALE_FILTER:
            cutoff = now - timedelta(hours=stale_after_hours)
            stmt = stmt.where(
                needs_elise,
                Thread.last_message_at.is_not(None),
                Thread.last_message_at < cutoff,
            )
        else:
            stmt = stmt.where(needs_elise)
    elif state == _FILTERED_FILTER:
        stmt = stmt.where(Thread.state.in_(_FILTERED_STATES))
    elif state == _REPLY_REVIEW_FILTER:
        latest_draft = _latest_draft_ranked()
        stmt = stmt.outerjoin(
            latest_draft,
            (latest_draft.c.tid == Thread.id) & (latest_draft.c.rn == 1),
        )
        stmt = stmt.where(_is_reply_review(latest_draft))
    elif state == _OPEN_FYI_FILTER:
        latest_draft = _latest_draft_ranked()
        stmt = stmt.outerjoin(
            latest_draft,
            (latest_draft.c.tid == Thread.id) & (latest_draft.c.rn == 1),
        )
        stmt = stmt.where(_is_fyi_briefing(latest_draft))
    elif state == _RECENTLY_RESOLVED_DRAFTASSISTANT_FILTER:
        stmt = stmt.where(_draftassistant_resolved_recently(now))
    elif state:
        stmt = stmt.where(Thread.state == state)
    if urgency:
        stmt = stmt.where(Thread.urgency == urgency)
    range_start, range_end = _mailbox_inclusive_day_bounds(date_from, date_to)
    if range_start is not None:
        stmt = stmt.where(
            Thread.last_message_at.is_not(None), Thread.last_message_at >= range_start
        )
    if range_end is not None:
        stmt = stmt.where(Thread.last_message_at.is_not(None), Thread.last_message_at < range_end)
    if stale_only:
        cutoff = now - timedelta(hours=stale_after_hours)
        stmt = stmt.where(
            Thread.last_message_at.is_not(None),
            Thread.last_message_at < cutoff,
            Thread.state.in_(_AWAITING_STATES),
        )
    if cursor:
        cursor_ts, cursor_id = _decode_cursor(cursor)
        if cursor_ts is not None:
            stmt = stmt.where(
                (Thread.last_message_at < cursor_ts)
                | ((Thread.last_message_at == cursor_ts) & (Thread.id < cursor_id))
            )
        else:
            stmt = stmt.where(Thread.id < cursor_id)
    return stmt


async def list_by_mailbox(
    session: AsyncSession,
    mailbox_email: str,
    *,
    state: str | None = None,
    urgency: str | None = None,
    stale_only: bool = False,
    stale_after_hours: int = 24,
    date_from: date | None = None,
    date_to: date | None = None,
    cursor: str | None = None,
    limit: int = 25,
) -> tuple[list[ThreadSummary], str | None]:
    """Cursor-paginated thread list for one mailbox (newest first)."""
    now = datetime.now(UTC)
    msg_count = func.count(Message.id).label("message_count")
    latest_msg = _latest_message_ranked()

    stmt = (
        select(
            Thread,
            msg_count,
            latest_msg.c.sender,
            latest_msg.c.direction,
            latest_msg.c.to_recipients,
            latest_msg.c.body_preview,
            latest_msg.c.body_text,
            latest_msg.c.unique_body_text,
            latest_msg.c.graph_message_id,
        )
        .outerjoin(Message, Message.thread_id == Thread.id)
        .outerjoin(
            latest_msg,
            (latest_msg.c.tid == Thread.id) & (latest_msg.c.rn == 1),
        )
        .where(Thread.mailbox == mailbox_email)
        .group_by(
            Thread.id,
            latest_msg.c.sender,
            latest_msg.c.direction,
            latest_msg.c.to_recipients,
            latest_msg.c.body_preview,
            latest_msg.c.body_text,
            latest_msg.c.unique_body_text,
            latest_msg.c.graph_message_id,
        )
        .order_by(Thread.last_message_at.desc().nullslast(), Thread.id.desc())
        .limit(limit + 1)
    )
    stmt = _apply_mailbox_list_filters(
        stmt,
        state=state,
        urgency=urgency,
        stale_only=stale_only,
        stale_after_hours=stale_after_hours,
        date_from=date_from,
        date_to=date_to,
        cursor=cursor,
        now=now,
    )

    result = await session.execute(stmt)
    rows = result.all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    items: list[ThreadSummary] = []
    pairs: list[tuple[str, str]] = []
    thread_ids: list[uuid.UUID] = []
    for (
        thread,
        count,
        sender,
        direction,
        to_recipients,
        body_preview,
        body_text,
        unique_body_text,
        graph_message_id,
    ) in rows:
        pairs.append((thread.mailbox, thread.conversation_id))
        thread_ids.append(thread.id)
        party = party_sender(thread.mailbox, sender, direction, to_recipients)
        items.append(
            ThreadSummary(
                id=thread.id,
                mailbox=thread.mailbox,
                mailbox_key=infer_mailbox_key(thread.mailbox),
                subject=thread.subject,
                state=display_state(thread.state, mailbox=thread.mailbox, last_sender=party),
                urgency=thread.urgency,
                urgency_reason=thread.urgency_reason,
                category=thread.category,
                last_message_at=thread.last_message_at,
                last_sender=party,
                preview=card_preview(body_text, unique_body_text, body_preview),
                staleness_hours=_staleness_hours(thread.last_message_at, now),
                message_count=int(count or 0),
                outlook_url=outlook_web_link(graph_message_id) if graph_message_id else None,
            )
        )

    triage_map = await audit_repo.triage_flags_by_conversations(session, pairs)
    automated_map = await message_repo.inbound_automated_by_threads(session, thread_ids)
    draft_ids = await _thread_ids_with_drafts(session, thread_ids)
    letter_ids = await _thread_ids_with_letters(session, thread_ids)
    teaching_notes = await draft_repo.latest_teaching_notes_by_threads(session, thread_ids)
    finished = await draft_repo.review_finished_by_threads(session, thread_ids)
    provenance = await audit_repo.resolution_provenance_by_conversations(session, pairs)
    for i, thread_row in enumerate(rows):
        thread = thread_row[0]
        prov = provenance.get((thread.mailbox, thread.conversation_id), {})
        items[i] = with_presentation(
            items[i].model_copy(
                update={
                    "triage": enriched_triage(
                        triage_map.get((thread.mailbox, thread.conversation_id)),
                        mailbox=thread.mailbox,
                        last_sender=items[i].last_sender,
                        subject=items[i].subject,
                        is_automated=automated_map.get(thread.id),
                    ),
                    "has_draft": thread.id in draft_ids,
                    "has_letter": thread.id in letter_ids,
                    "teaching_note": teaching_notes.get(thread.id),
                }
            ),
            draft_review_finished=thread.id in finished,
            resolved_by=prov.get("resolved_by"),
            resolution_reason=prov.get("resolution_reason"),
            resolution_summary=prov.get("resolution_summary"),
            forced_disposition=prov.get("forced_disposition"),
        )

    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = _encode_cursor(last.last_message_at, last.id)
    return items, next_cursor


async def list_recent_for_mailboxes(
    session: AsyncSession,
    mailbox_emails: list[str],
    *,
    per_mailbox: int = 3,
    stale_after_hours: int = 24,
) -> dict[str, list[ThreadSummary]]:
    """Top ``per_mailbox`` awaiting-action threads per mailbox.

    Ranked independently per mailbox so a busy inbox cannot starve the
    others on the home-page cards.
    """
    _ = stale_after_hours  # reserved for future stale filtering parity
    if not mailbox_emails or per_mailbox < 1:
        return {email: [] for email in mailbox_emails}

    now = datetime.now(UTC)
    msg_count = func.count(Message.id).label("message_count")
    latest_msg = _latest_message_ranked()
    latest_draft = _latest_draft_ranked()
    ranked = (
        select(
            Thread.id.label("tid"),
            func.row_number()
            .over(
                partition_by=Thread.mailbox,
                order_by=(Thread.last_message_at.desc().nullslast(), Thread.id.desc()),
            )
            .label("rn"),
        )
        .outerjoin(
            latest_draft,
            (latest_draft.c.tid == Thread.id) & (latest_draft.c.rn == 1),
        )
        .where(
            Thread.mailbox.in_(mailbox_emails),
            _needs_elise_action(latest_draft),
        )
        .subquery()
    )

    stmt = (
        select(
            Thread,
            msg_count,
            latest_msg.c.sender,
            latest_msg.c.direction,
            latest_msg.c.to_recipients,
            latest_msg.c.body_preview,
            latest_msg.c.body_text,
            latest_msg.c.unique_body_text,
            latest_msg.c.graph_message_id,
        )
        .join(ranked, ranked.c.tid == Thread.id)
        .outerjoin(Message, Message.thread_id == Thread.id)
        .outerjoin(
            latest_msg,
            (latest_msg.c.tid == Thread.id) & (latest_msg.c.rn == 1),
        )
        .where(ranked.c.rn <= per_mailbox)
        .group_by(
            Thread.id,
            latest_msg.c.sender,
            latest_msg.c.direction,
            latest_msg.c.to_recipients,
            latest_msg.c.body_preview,
            latest_msg.c.body_text,
            latest_msg.c.unique_body_text,
            latest_msg.c.graph_message_id,
        )
        .order_by(Thread.mailbox, Thread.last_message_at.desc().nullslast(), Thread.id.desc())
    )
    result = await session.execute(stmt)
    rows = result.all()

    buckets: dict[str, list[ThreadSummary]] = {email.lower(): [] for email in mailbox_emails}
    pairs: list[tuple[str, str]] = []
    thread_ids: list[uuid.UUID] = []
    pending: list[tuple[str, uuid.UUID, str]] = []
    for (
        thread,
        count,
        sender,
        direction,
        to_recipients,
        body_preview,
        body_text,
        unique_body_text,
        graph_message_id,
    ) in rows:
        key = thread.mailbox.lower()
        if key not in buckets or len(buckets[key]) >= per_mailbox:
            continue
        pairs.append((thread.mailbox, thread.conversation_id))
        thread_ids.append(thread.id)
        party = party_sender(thread.mailbox, sender, direction, to_recipients)
        summary = ThreadSummary(
            id=thread.id,
            mailbox=thread.mailbox,
            mailbox_key=infer_mailbox_key(thread.mailbox),
            subject=thread.subject,
            state=display_state(thread.state, mailbox=thread.mailbox, last_sender=party),
            urgency=thread.urgency,
            urgency_reason=thread.urgency_reason,
            category=thread.category,
            last_message_at=thread.last_message_at,
            last_sender=party,
            preview=card_preview(body_text, unique_body_text, body_preview),
            staleness_hours=_staleness_hours(thread.last_message_at, now),
            message_count=int(count or 0),
            outlook_url=outlook_web_link(graph_message_id) if graph_message_id else None,
        )
        buckets[key].append(summary)
        pending.append((thread.mailbox, thread.id, thread.conversation_id))

    triage_map = await audit_repo.triage_flags_by_conversations(session, pairs)
    automated_map = await message_repo.inbound_automated_by_threads(session, thread_ids)
    draft_ids = await _thread_ids_with_drafts(session, thread_ids)
    letter_ids = await _thread_ids_with_letters(session, thread_ids)
    teaching_notes = await draft_repo.latest_teaching_notes_by_threads(session, thread_ids)
    finished = await draft_repo.review_finished_by_threads(session, thread_ids)
    provenance = await audit_repo.resolution_provenance_by_conversations(session, pairs)
    for mailbox, thread_id, conversation_id in pending:
        key = mailbox.lower()
        for i, item in enumerate(buckets[key]):
            if item.id != thread_id:
                continue
            prov = provenance.get((mailbox, conversation_id), {})
            buckets[key][i] = with_presentation(
                item.model_copy(
                    update={
                        "triage": enriched_triage(
                            triage_map.get((mailbox, conversation_id)),
                            mailbox=mailbox,
                            last_sender=item.last_sender,
                            subject=item.subject,
                            is_automated=automated_map.get(thread_id),
                        ),
                        "has_draft": thread_id in draft_ids,
                        "has_letter": thread_id in letter_ids,
                        "teaching_note": teaching_notes.get(thread_id),
                    }
                ),
                draft_review_finished=thread_id in finished,
                resolved_by=prov.get("resolved_by"),
                resolution_reason=prov.get("resolution_reason"),
                resolution_summary=prov.get("resolution_summary"),
                forced_disposition=prov.get("forced_disposition"),
            )
            break

    return {email: buckets.get(email.lower(), []) for email in mailbox_emails}


async def _thread_ids_with_drafts(
    session: AsyncSession,
    thread_ids: list[uuid.UUID],
) -> set[uuid.UUID]:
    if not thread_ids:
        return set()
    stmt = select(Draft.thread_id).where(Draft.thread_id.in_(thread_ids)).distinct()
    result = await session.execute(stmt)
    return {row[0] for row in result.all()}


async def ids_needing_attention(
    session: AsyncSession,
    thread_ids: Sequence[uuid.UUID],
) -> set[uuid.UUID]:
    """Subset of ``thread_ids`` that still belong on Needs Attention."""
    if not thread_ids:
        return set()
    latest_draft = _latest_draft_ranked()
    stmt = (
        select(Thread.id)
        .outerjoin(
            latest_draft,
            (latest_draft.c.tid == Thread.id) & (latest_draft.c.rn == 1),
        )
        .where(
            Thread.id.in_(list(thread_ids)),
            _needs_elise_action(latest_draft),
        )
    )
    result = await session.execute(stmt)
    return {row[0] for row in result.all()}


def _latest_draft_ranked() -> Any:
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


def _draft_unanswered(latest_draft: Any) -> Any:
    return and_(
        latest_draft.c.approved_at.is_(None),
        or_(
            latest_draft.c.feedback_action.is_(None),
            latest_draft.c.feedback_action != "wrong",
        ),
    )


def _has_letter_sql(latest_draft: Any) -> Any:
    return and_(
        latest_draft.c.tid.is_not(None),
        func.length(func.btrim(func.coalesce(latest_draft.c.body, ""))) > 0,
    )


def _no_letter_sql(latest_draft: Any) -> Any:
    return or_(
        latest_draft.c.tid.is_(None),
        func.length(func.btrim(func.coalesce(latest_draft.c.body, ""))) == 0,
    )


def _is_operational_alert() -> Any:
    return or_(
        Thread.alert_fingerprint.is_not(None),
        exists(
            select(1).where(
                _AutomatedMessage.thread_id == Thread.id,
                _AutomatedMessage.is_automated.is_(True),
            )
        ),
    )


_TRIAGE_EVENT_TYPES = (
    TriageAuditEvent.ACTION_NEEDED.value,
    TriageAuditEvent.NO_ACTION_DISCARDED.value,
    TriageAuditEvent.SPAM_DISCARDED.value,
    TriageAuditEvent.FAILED.value,
)


def _triage_informational() -> Any:
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


def _is_actionable_operational_alert() -> Any:
    """Ops automated alert that still needs Elise — not domain-auth / FYI triage."""
    return and_(_is_operational_alert(), ~_triage_informational())


def _is_reply_review(latest_draft: Any) -> Any:
    """Disposition reply_review: DRAFTED with non-empty letter still awaiting Elise."""
    return and_(
        Thread.state == ThreadStateEnum.DRAFTED.value,
        _has_letter_sql(latest_draft),
        _draft_unanswered(latest_draft),
    )


def _needs_elise_action(latest_draft: Any) -> Any:
    """SQL: Needs Attention dispositions — letter review, offline action, or pipeline failure."""
    unanswered = _draft_unanswered(latest_draft)
    no_draft = latest_draft.c.tid.is_(None)
    requires_human = and_(
        Thread.state == ThreadStateEnum.REQUIRES_HUMAN.value,
        or_(no_draft, unanswered),
    )
    action_no_draft = and_(
        Thread.state == ThreadStateEnum.DRAFTED.value,
        _no_letter_sql(latest_draft),
        or_(no_draft, unanswered),
        _is_actionable_operational_alert(),
    )
    return or_(requires_human, _is_reply_review(latest_draft), action_no_draft)


def _is_fyi_briefing(latest_draft: Any) -> Any:
    unanswered = _draft_unanswered(latest_draft)
    no_draft = latest_draft.c.tid.is_(None)
    return and_(
        Thread.state == ThreadStateEnum.DRAFTED.value,
        _no_letter_sql(latest_draft),
        or_(no_draft, unanswered),
        or_(~_is_operational_alert(), _triage_informational()),
    )


def _draftassistant_resolved_recently(now: datetime, hours: int = _RECENTLY_RESOLVED_DRAFTASSISTANT_HOURS) -> Any:
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


async def _thread_ids_with_letters(
    session: AsyncSession,
    thread_ids: list[uuid.UUID],
) -> set[uuid.UUID]:
    if not thread_ids:
        return set()
    stmt = (
        select(Draft.thread_id)
        .where(
            Draft.thread_id.in_(thread_ids),
            func.length(func.btrim(func.coalesce(Draft.body, ""))) > 0,
        )
        .distinct()
    )
    result = await session.execute(stmt)
    return {row[0] for row in result.all()}


async def list_needs_attention(
    session: AsyncSession,
    mailbox_emails: list[str],
    *,
    limit: int = 20,
    sort: Literal["urgency", "recent"] = "urgency",
) -> list[ThreadSummary]:
    """Awaiting-action threads across configured mailboxes."""
    return await _list_thread_summaries(
        session,
        mailbox_emails,
        match=lambda latest_draft, now: _needs_elise_action(latest_draft),
        limit=limit,
        sort=sort,
    )


async def _list_thread_summaries(
    session: AsyncSession,
    mailbox_emails: list[str],
    *,
    match: Any,
    limit: int,
    sort: Literal["urgency", "recent"] = "urgency",
) -> list[ThreadSummary]:
    """Shared mailbox-list assembly for Needs Attention / Open FYI / DraftAssistant resolved."""
    if not mailbox_emails:
        return []
    now = datetime.now(UTC)
    msg_count = func.count(Message.id).label("message_count")
    latest_msg = _latest_message_ranked()
    latest_draft = _latest_draft_ranked()

    urgency_rank = case(
        (Thread.urgency == "CRITICAL", 0),
        (Thread.urgency == "HIGH", 1),
        (Thread.urgency == "NORMAL", 2),
        (Thread.urgency == "LOW", 3),
        else_=4,
    )

    stmt = (
        select(
            Thread,
            msg_count,
            latest_msg.c.sender,
            latest_msg.c.direction,
            latest_msg.c.to_recipients,
            latest_msg.c.body_preview,
            latest_msg.c.body_text,
            latest_msg.c.unique_body_text,
            latest_msg.c.graph_message_id,
        )
        .outerjoin(Message, Message.thread_id == Thread.id)
        .outerjoin(
            latest_msg,
            (latest_msg.c.tid == Thread.id) & (latest_msg.c.rn == 1),
        )
        .outerjoin(
            latest_draft,
            (latest_draft.c.tid == Thread.id) & (latest_draft.c.rn == 1),
        )
        .where(
            Thread.mailbox.in_(mailbox_emails),
            match(latest_draft, now),
        )
        .group_by(
            Thread.id,
            latest_msg.c.sender,
            latest_msg.c.direction,
            latest_msg.c.to_recipients,
            latest_msg.c.body_preview,
            latest_msg.c.body_text,
            latest_msg.c.unique_body_text,
            latest_msg.c.graph_message_id,
        )
    )
    if sort == "recent":
        stmt = stmt.order_by(Thread.last_message_at.desc().nullslast())
    else:
        stmt = stmt.order_by(urgency_rank.asc(), Thread.last_message_at.desc().nullslast())
    stmt = stmt.limit(limit)
    result = await session.execute(stmt)
    rows = result.all()
    summaries: list[ThreadSummary] = []
    pairs: list[tuple[str, str]] = []
    thread_ids: list[uuid.UUID] = []
    for (
        thread,
        count,
        sender,
        direction,
        to_recipients,
        body_preview,
        body_text,
        unique_body_text,
        graph_message_id,
    ) in rows:
        pairs.append((thread.mailbox, thread.conversation_id))
        thread_ids.append(thread.id)
        party = party_sender(thread.mailbox, sender, direction, to_recipients)
        summaries.append(
            ThreadSummary(
                id=thread.id,
                mailbox=thread.mailbox,
                mailbox_key=infer_mailbox_key(thread.mailbox),
                subject=thread.subject,
                state=display_state(thread.state, mailbox=thread.mailbox, last_sender=party),
                urgency=thread.urgency,
                urgency_reason=thread.urgency_reason,
                category=thread.category,
                last_message_at=thread.last_message_at,
                last_sender=party,
                preview=card_preview(body_text, unique_body_text, body_preview),
                staleness_hours=_staleness_hours(thread.last_message_at, now),
                message_count=int(count or 0),
                outlook_url=outlook_web_link(graph_message_id) if graph_message_id else None,
            )
        )
    triage_map = await audit_repo.triage_flags_by_conversations(session, pairs)
    automated_map = await message_repo.inbound_automated_by_threads(session, thread_ids)
    draft_ids = await _thread_ids_with_drafts(session, thread_ids)
    letter_ids = await _thread_ids_with_letters(session, thread_ids)
    teaching_notes = await draft_repo.latest_teaching_notes_by_threads(session, thread_ids)
    finished = await draft_repo.review_finished_by_threads(session, thread_ids)
    provenance = await audit_repo.resolution_provenance_by_conversations(session, pairs)
    for i, thread_row in enumerate(rows):
        thread = thread_row[0]
        prov = provenance.get((thread.mailbox, thread.conversation_id), {})
        summaries[i] = with_presentation(
            summaries[i].model_copy(
                update={
                    "triage": enriched_triage(
                        triage_map.get((thread.mailbox, thread.conversation_id)),
                        mailbox=thread.mailbox,
                        last_sender=summaries[i].last_sender,
                        subject=summaries[i].subject,
                        is_automated=automated_map.get(thread.id),
                    ),
                    "has_draft": thread.id in draft_ids,
                    "has_letter": thread.id in letter_ids,
                    "teaching_note": teaching_notes.get(thread.id),
                }
            ),
            draft_review_finished=thread.id in finished,
            resolved_by=prov.get("resolved_by"),
            resolution_reason=prov.get("resolution_reason"),
            resolution_summary=prov.get("resolution_summary"),
            forced_disposition=prov.get("forced_disposition"),
        )
    return summaries


async def list_open_fyi(
    session: AsyncSession,
    mailbox_emails: list[str],
    *,
    limit: int = 20,
) -> list[ThreadSummary]:
    """Open FYI briefings across configured mailboxes."""
    return await _list_thread_summaries(
        session,
        mailbox_emails,
        match=lambda latest_draft, now: _is_fyi_briefing(latest_draft),
        limit=limit,
        sort="recent",
    )


async def list_recently_resolved_by_draftassistant(
    session: AsyncSession,
    mailbox_emails: list[str],
    *,
    limit: int = 20,
) -> list[ThreadSummary]:
    """DraftAssistant resolves in the last 48 hours (review queue)."""
    return await _list_thread_summaries(
        session,
        mailbox_emails,
        match=lambda latest_draft, now: _draftassistant_resolved_recently(now),
        limit=limit,
        sort="recent",
    )


async def aggregate_overview(
    session: AsyncSession,
    mailbox_emails: list[str],
    *,
    stale_after_hours: int = 24,
) -> list[dict[str, Any]]:
    """Per-mailbox counts for the home dashboard."""
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=stale_after_hours)
    if not mailbox_emails:
        return []

    latest_draft = _latest_draft_ranked()
    needs_elise = _needs_elise_action(latest_draft)
    open_fyi = _is_fyi_briefing(latest_draft)
    draftassistant_recent = _draftassistant_resolved_recently(now)
    stmt = (
        select(
            Thread.mailbox,
            func.count(Thread.id).label("thread_count"),
            func.count(case((needs_elise, 1))).label("awaiting_action_count"),
            func.count(case((Thread.state.in_(_FILTERED_STATES), 1))).label("filtered_count"),
            func.count(
                case(
                    (
                        (Thread.last_message_at < cutoff) & needs_elise,
                        1,
                    )
                )
            ).label("stale_count"),
            func.count(case((open_fyi, 1))).label("open_fyi_count"),
            func.count(case((draftassistant_recent, 1))).label("recently_resolved_draftassistant_count"),
            func.count(case((and_(needs_elise, Thread.urgency == "CRITICAL"), 1))).label(
                "urgency_critical"
            ),
            func.count(case((and_(needs_elise, Thread.urgency == "HIGH"), 1))).label(
                "urgency_high"
            ),
            func.count(case((and_(needs_elise, Thread.urgency == "NORMAL"), 1))).label(
                "urgency_normal"
            ),
            func.count(case((and_(needs_elise, Thread.urgency == "LOW"), 1))).label(
                "urgency_low"
            ),
        )
        .outerjoin(
            latest_draft,
            (latest_draft.c.tid == Thread.id) & (latest_draft.c.rn == 1),
        )
        .where(Thread.mailbox.in_(mailbox_emails))
        .group_by(Thread.mailbox)
    )
    result = await session.execute(stmt)
    out: list[dict[str, Any]] = []
    for row in result.all():
        out.append(
            {
                "mailbox": row.mailbox,
                "thread_count": int(row.thread_count or 0),
                "awaiting_action_count": int(row.awaiting_action_count or 0),
                "filtered_count": int(row.filtered_count or 0),
                "stale_count": int(row.stale_count or 0),
                "open_fyi_count": int(row.open_fyi_count or 0),
                "recently_resolved_draftassistant_count": int(row.recently_resolved_draftassistant_count or 0),
                "urgency_breakdown": {
                    "CRITICAL": int(row.urgency_critical or 0),
                    "HIGH": int(row.urgency_high or 0),
                    "NORMAL": int(row.urgency_normal or 0),
                    "LOW": int(row.urgency_low or 0),
                },
            }
        )
    return out


async def build_thread_summary(
    session: AsyncSession,
    thread: ThreadSchema,
) -> ThreadSummary:
    """Assemble a ThreadSummary for a single known thread."""
    now = datetime.now(UTC)
    count_stmt = select(func.count(Message.id)).where(Message.thread_id == thread.id)
    count = int((await session.execute(count_stmt)).scalar_one() or 0)
    latest_stmt = (
        select(
            Message.sender,
            Message.body_preview,
            Message.body_text,
            Message.unique_body_text,
            Message.graph_message_id,
            Message.direction,
            Message.to_recipients,
        )
        .where(Message.thread_id == thread.id)
        .order_by(Message.received_at.desc())
        .limit(1)
    )
    latest = (await session.execute(latest_stmt)).one_or_none()
    sender = latest[0] if latest else None
    body_preview = latest[1] if latest else None
    body_text = latest[2] if latest else None
    unique_body_text = latest[3] if latest else None
    graph_message_id = latest[4] if latest else None
    direction = latest[5] if latest else None
    to_recipients = latest[6] if latest else None
    party = party_sender(thread.mailbox, sender, direction, to_recipients)
    teaching_notes = await draft_repo.latest_teaching_notes_by_threads(session, [thread.id])
    finished = await draft_repo.review_finished_by_threads(session, [thread.id])
    letter_ids = await _thread_ids_with_letters(session, [thread.id])
    provenance = await audit_repo.resolution_provenance_by_conversations(
        session, [(thread.mailbox, thread.conversation_id)]
    )
    prov = provenance.get((thread.mailbox, thread.conversation_id), {})
    summary = ThreadSummary(
        id=thread.id,
        mailbox=thread.mailbox,
        mailbox_key=infer_mailbox_key(thread.mailbox),
        subject=thread.subject,
        state=display_state(thread.state, mailbox=thread.mailbox, last_sender=party),
        urgency=thread.urgency,
        urgency_reason=thread.urgency_reason,
        category=thread.category,
        last_message_at=thread.last_message_at,
        last_sender=party,
        preview=card_preview(body_text, unique_body_text, body_preview),
        staleness_hours=_staleness_hours(thread.last_message_at, now),
        message_count=count,
        has_draft=thread.id in await _thread_ids_with_drafts(session, [thread.id]),
        has_letter=thread.id in letter_ids,
        teaching_note=teaching_notes.get(thread.id),
        triage=enriched_triage(
            await audit_repo.get_latest_triage_flags(
                session,
                thread.conversation_id,
                mailbox=thread.mailbox,
            ),
            mailbox=thread.mailbox,
            last_sender=party,
            subject=thread.subject,
            is_automated=(
                await message_repo.inbound_automated_by_threads(session, [thread.id])
            ).get(thread.id),
        ),
        outlook_url=outlook_web_link(graph_message_id) if graph_message_id else None,
    )
    return with_presentation(
        summary,
        draft_review_finished=thread.id in finished,
        resolved_by=prov.get("resolved_by"),
        resolution_reason=prov.get("resolution_reason"),
        resolution_summary=prov.get("resolution_summary"),
        forced_disposition=prov.get("forced_disposition"),
    )
