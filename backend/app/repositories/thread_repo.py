"""Thread repository — async CRUD returning Pydantic schemas."""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Select, and_, case, exists, func, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InvalidCursorError
from app.core.internal_mail import (
    display_state_for_internal_mail,
    enrich_triage_flags,
    thread_counterpart,
)
from app.core.mailbox_keys import infer_mailbox_key
from app.core.outlook_links import outlook_web_link
from app.models.db.draft import Draft
from app.models.db.message import Message
from app.models.db.thread import Thread
from app.models.schemas.dashboard import ThreadSummary, TriageFlags
from app.models.schemas.email import ThreadStateEnum
from app.repositories import audit_repo, draft_repo

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

#: Terminal triage outcomes that carry no action for Elise — filtered out of
#: the default mailbox/dashboard views (still queryable via
#: ``include_filtered=True`` for transparency/audit).
_FILTERED_STATES = (
    ThreadStateEnum.SPAM.value,
    ThreadStateEnum.NO_ACTION.value,
)


def _enriched_triage(
    flags: TriageFlags | None,
    *,
    mailbox: str,
    last_sender: str | None,
    subject: str | None = None,
) -> TriageFlags | None:
    return enrich_triage_flags(flags, sender=last_sender, mailbox=mailbox, subject=subject)


def _display_state(state: str, *, mailbox: str, last_sender: str | None) -> str:
    return display_state_for_internal_mail(state, sender=last_sender, mailbox=mailbox)


def _latest_message_ranked():
    return (
        select(
            Message.thread_id.label("tid"),
            Message.sender.label("sender"),
            Message.direction.label("direction"),
            Message.to_recipients.label("to_recipients"),
            Message.body_preview.label("preview"),
            Message.graph_message_id.label("graph_message_id"),
            func.row_number()
            .over(
                partition_by=Message.thread_id,
                order_by=Message.received_at.desc(),
            )
            .label("rn"),
        )
    ).subquery()


def _party_sender(
    mailbox: str,
    sender: str | None,
    direction: str | None,
    to_recipients: list[str] | None,
) -> str | None:
    return thread_counterpart(
        mailbox=mailbox,
        sender=sender,
        direction=direction,
        to_recipients=list(to_recipients or []),
    )


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


async def get_by_id(session: AsyncSession, thread_id: uuid.UUID) -> ThreadSchema | None:
    stmt = select(Thread).where(Thread.id == thread_id)
    result = await session.execute(stmt)
    thread = result.scalar_one_or_none()
    if thread is None:
        return None
    return ThreadSchema.model_validate(thread)


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


def _folded_text(column: object):
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
            "last_message_at": last_message_at,
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


async def list_by_mailbox(
    session: AsyncSession,
    mailbox_email: str,
    *,
    state: str | None = None,
    urgency: str | None = None,
    stale_only: bool = False,
    stale_after_hours: int = 24,
    include_filtered: bool = False,
    cursor: str | None = None,
    limit: int = 25,
) -> tuple[list[ThreadSummary], str | None]:
    """Cursor-paginated thread list for one mailbox (newest first)."""
    now = datetime.now(UTC)
    msg_count = func.count(Message.id).label("message_count")
    latest_msg = _latest_message_ranked()

    stmt: Select[
        tuple[Thread, int, str | None, str | None, list[str] | None, str | None, str | None]
    ] = (
        select(
            Thread,
            msg_count,
            latest_msg.c.sender,
            latest_msg.c.direction,
            latest_msg.c.to_recipients,
            latest_msg.c.preview,
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
            latest_msg.c.preview,
            latest_msg.c.graph_message_id,
        )
        .order_by(Thread.last_message_at.desc().nullslast(), Thread.id.desc())
        .limit(limit + 1)
    )
    if state:
        stmt = stmt.where(Thread.state == state)
    elif not include_filtered:
        stmt = stmt.where(Thread.state.notin_(_FILTERED_STATES))
    if urgency:
        stmt = stmt.where(Thread.urgency == urgency)
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

    result = await session.execute(stmt)
    rows = result.all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    items: list[ThreadSummary] = []
    pairs: list[tuple[str, str]] = []
    thread_ids: list[uuid.UUID] = []
    for thread, count, sender, direction, to_recipients, preview, graph_message_id in rows:
        pairs.append((thread.mailbox, thread.conversation_id))
        thread_ids.append(thread.id)
        party = _party_sender(thread.mailbox, sender, direction, to_recipients)
        items.append(
            ThreadSummary(
                id=thread.id,
                mailbox=thread.mailbox,
                mailbox_key=infer_mailbox_key(thread.mailbox),
                subject=thread.subject,
                state=_display_state(thread.state, mailbox=thread.mailbox, last_sender=party),
                urgency=thread.urgency,
                urgency_reason=thread.urgency_reason,
                category=thread.category,
                last_message_at=thread.last_message_at,
                last_sender=party,
                preview=preview,
                staleness_hours=_staleness_hours(thread.last_message_at, now),
                message_count=int(count or 0),
                outlook_url=outlook_web_link(graph_message_id) if graph_message_id else None,
            )
        )

    triage_map = await audit_repo.triage_flags_by_conversations(session, pairs)
    draft_ids = await _thread_ids_with_drafts(session, thread_ids)
    teaching_notes = await draft_repo.latest_teaching_notes_by_threads(session, thread_ids)
    for i, thread_row in enumerate(rows):
        thread = thread_row[0]
        items[i] = items[i].model_copy(
            update={
                "triage": _enriched_triage(
                    triage_map.get((thread.mailbox, thread.conversation_id)),
                    mailbox=thread.mailbox,
                    last_sender=items[i].last_sender,
                    subject=items[i].subject,
                ),
                "has_draft": thread.id in draft_ids,
                "teaching_note": teaching_notes.get(thread.id),
            }
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
    """Top ``per_mailbox`` recent threads per mailbox in a single capped query."""
    _ = stale_after_hours  # reserved for future stale filtering parity
    if not mailbox_emails or per_mailbox < 1:
        return {email: [] for email in mailbox_emails}

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
            latest_msg.c.preview,
            latest_msg.c.graph_message_id,
        )
        .outerjoin(Message, Message.thread_id == Thread.id)
        .outerjoin(
            latest_msg,
            (latest_msg.c.tid == Thread.id) & (latest_msg.c.rn == 1),
        )
        .where(
            Thread.mailbox.in_(mailbox_emails),
            Thread.state.notin_(_FILTERED_STATES),
        )
        .group_by(
            Thread.id,
            latest_msg.c.sender,
            latest_msg.c.direction,
            latest_msg.c.to_recipients,
            latest_msg.c.preview,
            latest_msg.c.graph_message_id,
        )
        .order_by(Thread.mailbox, Thread.last_message_at.desc().nullslast())
        .limit(len(mailbox_emails) * per_mailbox * 2)
    )
    result = await session.execute(stmt)
    rows = result.all()

    buckets: dict[str, list[ThreadSummary]] = {email.lower(): [] for email in mailbox_emails}
    pairs: list[tuple[str, str]] = []
    thread_ids: list[uuid.UUID] = []
    pending: list[tuple[str, uuid.UUID, str]] = []
    for thread, count, sender, direction, to_recipients, preview, graph_message_id in rows:
        key = thread.mailbox.lower()
        if key not in buckets or len(buckets[key]) >= per_mailbox:
            continue
        pairs.append((thread.mailbox, thread.conversation_id))
        thread_ids.append(thread.id)
        party = _party_sender(thread.mailbox, sender, direction, to_recipients)
        summary = ThreadSummary(
            id=thread.id,
            mailbox=thread.mailbox,
            mailbox_key=infer_mailbox_key(thread.mailbox),
            subject=thread.subject,
            state=_display_state(thread.state, mailbox=thread.mailbox, last_sender=party),
            urgency=thread.urgency,
            urgency_reason=thread.urgency_reason,
            category=thread.category,
            last_message_at=thread.last_message_at,
            last_sender=party,
            preview=preview,
            staleness_hours=_staleness_hours(thread.last_message_at, now),
            message_count=int(count or 0),
            outlook_url=outlook_web_link(graph_message_id) if graph_message_id else None,
        )
        buckets[key].append(summary)
        pending.append((thread.mailbox, thread.id, thread.conversation_id))

    triage_map = await audit_repo.triage_flags_by_conversations(session, pairs)
    draft_ids = await _thread_ids_with_drafts(session, thread_ids)
    teaching_notes = await draft_repo.latest_teaching_notes_by_threads(session, thread_ids)
    for mailbox, thread_id, conversation_id in pending:
        key = mailbox.lower()
        for i, item in enumerate(buckets[key]):
            if item.id != thread_id:
                continue
            buckets[key][i] = item.model_copy(
                update={
                    "triage": _enriched_triage(
                        triage_map.get((mailbox, conversation_id)),
                        mailbox=mailbox,
                        last_sender=item.last_sender,
                        subject=item.subject,
                    ),
                    "has_draft": thread_id in draft_ids,
                    "teaching_note": teaching_notes.get(thread_id),
                }
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


def _latest_draft_ranked():
    return (
        select(
            Draft.thread_id.label("tid"),
            Draft.feedback_action.label("feedback_action"),
            Draft.approved_at.label("approved_at"),
            func.row_number()
            .over(
                partition_by=Draft.thread_id,
                order_by=(Draft.created_at.desc(), Draft.id.desc()),
            )
            .label("rn"),
        )
    ).subquery()


def _needs_elise_action(latest_draft):
    """SQL: DRAFTED/REQUIRES_HUMAN and latest draft is not approved or no-reply."""
    no_draft = latest_draft.c.tid.is_(None)
    unanswered = and_(
        latest_draft.c.approved_at.is_(None),
        or_(
            latest_draft.c.feedback_action.is_(None),
            latest_draft.c.feedback_action != "wrong",
        ),
    )
    return and_(
        Thread.state.in_(_NEEDS_ATTENTION_STATES),
        or_(no_draft, unanswered),
    )


async def list_needs_attention(
    session: AsyncSession,
    mailbox_emails: list[str],
    *,
    limit: int = 20,
) -> list[ThreadSummary]:
    """Newest awaiting-action threads across configured mailboxes."""
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
            latest_msg.c.preview,
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
            _needs_elise_action(latest_draft),
        )
        .group_by(
            Thread.id,
            latest_msg.c.sender,
            latest_msg.c.direction,
            latest_msg.c.to_recipients,
            latest_msg.c.preview,
            latest_msg.c.graph_message_id,
        )
        .order_by(urgency_rank.asc(), Thread.last_message_at.desc().nullslast())
        .limit(limit)
    )
    result = await session.execute(stmt)
    rows = result.all()
    summaries: list[ThreadSummary] = []
    pairs: list[tuple[str, str]] = []
    thread_ids: list[uuid.UUID] = []
    for thread, count, sender, direction, to_recipients, preview, graph_message_id in rows:
        pairs.append((thread.mailbox, thread.conversation_id))
        thread_ids.append(thread.id)
        party = _party_sender(thread.mailbox, sender, direction, to_recipients)
        summaries.append(
            ThreadSummary(
                id=thread.id,
                mailbox=thread.mailbox,
                mailbox_key=infer_mailbox_key(thread.mailbox),
                subject=thread.subject,
                state=_display_state(thread.state, mailbox=thread.mailbox, last_sender=party),
                urgency=thread.urgency,
                urgency_reason=thread.urgency_reason,
                category=thread.category,
                last_message_at=thread.last_message_at,
                last_sender=party,
                preview=preview,
                staleness_hours=_staleness_hours(thread.last_message_at, now),
                message_count=int(count or 0),
                outlook_url=outlook_web_link(graph_message_id) if graph_message_id else None,
            )
        )
    triage_map = await audit_repo.triage_flags_by_conversations(session, pairs)
    draft_ids = await _thread_ids_with_drafts(session, thread_ids)
    teaching_notes = await draft_repo.latest_teaching_notes_by_threads(session, thread_ids)
    for i, thread_row in enumerate(rows):
        thread = thread_row[0]
        summaries[i] = summaries[i].model_copy(
            update={
                "triage": _enriched_triage(
                    triage_map.get((thread.mailbox, thread.conversation_id)),
                    mailbox=thread.mailbox,
                    last_sender=summaries[i].last_sender,
                    subject=summaries[i].subject,
                ),
                "has_draft": thread.id in draft_ids,
                "teaching_note": teaching_notes.get(thread.id),
            }
        )
    return summaries


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
            func.count(case((Thread.urgency == "CRITICAL", 1))).label("urgency_critical"),
            func.count(case((Thread.urgency == "HIGH", 1))).label("urgency_high"),
            func.count(case((Thread.urgency == "NORMAL", 1))).label("urgency_normal"),
            func.count(case((Thread.urgency == "LOW", 1))).label("urgency_low"),
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
    preview = latest[1] if latest else None
    graph_message_id = latest[2] if latest else None
    direction = latest[3] if latest else None
    to_recipients = latest[4] if latest else None
    party = _party_sender(thread.mailbox, sender, direction, to_recipients)
    teaching_notes = await draft_repo.latest_teaching_notes_by_threads(session, [thread.id])
    return ThreadSummary(
        id=thread.id,
        mailbox=thread.mailbox,
        mailbox_key=infer_mailbox_key(thread.mailbox),
        subject=thread.subject,
        state=_display_state(thread.state, mailbox=thread.mailbox, last_sender=party),
        urgency=thread.urgency,
        urgency_reason=thread.urgency_reason,
        category=thread.category,
        last_message_at=thread.last_message_at,
        last_sender=party,
        preview=preview,
        staleness_hours=_staleness_hours(thread.last_message_at, now),
        message_count=count,
        has_draft=thread.id in await _thread_ids_with_drafts(session, [thread.id]),
        teaching_note=teaching_notes.get(thread.id),
        triage=_enriched_triage(
            await audit_repo.get_latest_triage_flags(
                session,
                thread.conversation_id,
                mailbox=thread.mailbox,
            ),
            mailbox=thread.mailbox,
            last_sender=party,
            subject=thread.subject,
        ),
        outlook_url=outlook_web_link(graph_message_id) if graph_message_id else None,
    )
