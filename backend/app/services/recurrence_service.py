"""Recurring automated-alert detection, cluster floor, and urgency HITL."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.automated_mail import is_automated_mail
from app.core.exceptions import ThreadStateError
from app.core.thread_policy import max_urgency, recurrence_urgency_floor
from app.models.schemas.email import ThreadStateEnum
from app.repositories import (
    alert_fingerprint_feedback_repo,
    audit_repo,
    draft_repo,
    message_repo,
    thread_repo,
)
from app.services import audit_service
from app.services.related_match import alert_cluster_keys

logger = structlog.get_logger(__name__)

_FINISHED = frozenset(
    {
        ThreadStateEnum.RESOLVED.value,
        ThreadStateEnum.NO_ACTION.value,
        ThreadStateEnum.SPAM.value,
    }
)
_WINDOW = timedelta(hours=48)
_ESCALATED = "thread.urgency.recurrence_escalated"
_WRONG = "thread.urgency.recurrence_wrong"


def _bump_reason(count: int, applied: str) -> str:
    return (
        f"Urgency bumped automatically: {count} similar alerts in 48h "
        f"(same sender and subject). Urgency raised to {applied}."
    )


def _clock(now: datetime | None) -> datetime:
    clock = now or datetime.now(UTC)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=UTC)
    return clock


def _count_automated_in_rows(rows, *, cutoff: datetime) -> int:
    count = 0
    for row in rows:
        if str(row.direction).lower() != "inbound":
            continue
        received = row.received_at
        if received.tzinfo is None:
            received = received.replace(tzinfo=UTC)
        if received < cutoff:
            continue
        sender = row.sender
        if is_automated_mail(sender=sender, subject=None):
            count += 1
            continue
        preview = (row.body_preview or "")[:120]
        if is_automated_mail(sender=sender, subject=preview):
            count += 1
    return count


async def count_automated_inbound_48h(
    session: AsyncSession,
    thread_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> int:
    """Count inbound automated messages in the last 48 hours for a thread."""
    clock = _clock(now)
    cutoff = clock - _WINDOW
    rows = await message_repo.list_by_thread(session, thread_id)
    return _count_automated_in_rows(rows, cutoff=cutoff)


async def _latest_inbound_sender(session: AsyncSession, thread_id: uuid.UUID) -> str:
    rows = await message_repo.list_by_thread(session, thread_id)
    inbound = [row for row in rows if str(row.direction).lower() == "inbound"]
    if inbound:
        return inbound[-1].sender
    if rows:
        return rows[-1].sender
    return ""


async def _ensure_fingerprint(session: AsyncSession, thread) -> str | None:
    if (
        thread.alert_fingerprint
        and thread.alert_signature
        and thread.alert_sender_norm
    ):
        return thread.alert_fingerprint
    sender = await _latest_inbound_sender(session, thread.id)
    keys = alert_cluster_keys(
        mailbox=thread.mailbox,
        sender=sender,
        subject=thread.subject,
    )
    if keys is None:
        return thread.alert_fingerprint
    fingerprint, signature, sender_norm = keys
    await thread_repo.set_alert_fingerprint(
        session,
        thread.id,
        fingerprint,
        signature=signature,
        sender_norm=sender_norm,
    )
    thread.alert_fingerprint = fingerprint
    thread.alert_signature = signature
    thread.alert_sender_norm = sender_norm
    return fingerprint


async def _open_cluster(session: AsyncSession, thread, *, now: datetime):
    fingerprint = thread.alert_fingerprint
    if not fingerprint:
        return [thread], 0
    rows = await thread_repo.list_open_alert_cluster(
        session,
        mailbox=thread.mailbox,
        fingerprint=fingerprint,
        signature=thread.alert_signature,
        sender_norm=thread.alert_sender_norm,
        now=now,
        for_update=True,
    )
    by_id = {row.id: row for row in rows}
    by_id[thread.id] = thread
    grouped = await message_repo.list_by_thread_ids(session, list(by_id.keys()))
    cutoff = now - _WINDOW
    members = []
    grain = 0
    for row in by_id.values():
        inbound = _count_automated_in_rows(grouped.get(row.id, []), cutoff=cutoff)
        if inbound < 1 and row.id != thread.id:
            continue
        members.append(row)
        grain += inbound
    if not any(row.id == thread.id for row in members):
        members.insert(0, thread)
        grain += _count_automated_in_rows(grouped.get(thread.id, []), cutoff=cutoff)
    return members, grain


async def _apply_to_thread(
    session: AsyncSession,
    *,
    thread_id: uuid.UUID,
    conversation_id: str,
    mailbox: str,
    applied: str,
    reason: str,
    previous: str | None,
    floor: str | None,
    count: int,
    cluster_ids: list[str],
    scope: str,
) -> None:
    await thread_repo.set_urgency(
        session,
        thread_id,
        urgency=applied,
        urgency_reason=reason,
    )
    draft = await draft_repo.get_latest_by_thread(session, thread_id)
    if draft is not None:
        await draft_repo.set_urgency(
            session,
            draft.id,
            urgency=applied,
            urgency_reason=reason,
        )
    await audit_service.log_event(
        session,
        event_type=_ESCALATED,
        conversation_id=conversation_id,
        mailbox=mailbox,
        payload={
            "floor": floor,
            "count_48h": count,
            "previous": previous,
            "new": applied,
            "cluster_thread_ids": cluster_ids,
            "scope": scope,
            "human": {
                "title": "Urgency raised for recurring alert",
                "body": reason,
                "actor_kind": "agent",
            },
        },
        actor="system",
    )


async def apply_recurrence_escalation(
    session: AsyncSession,
    *,
    thread_id: uuid.UUID,
    conversation_id: str,
    mailbox: str,
    assessed_urgency: str | None,
    now: datetime | None = None,
) -> str | None:
    """Raise open-cluster urgency to max(assessed, member max, count floor)."""
    clock = _clock(now)
    thread = await thread_repo.get_by_id(session, thread_id)
    if thread is None:
        return assessed_urgency
    if thread.state in _FINISHED:
        return assessed_urgency

    fingerprint = await _ensure_fingerprint(session, thread)
    if fingerprint and await alert_fingerprint_feedback_repo.is_suppressed(
        session, mailbox=thread.mailbox, fingerprint=fingerprint
    ):
        logger.info(
            "recurrence_suppressed",
            thread_id=str(thread_id),
            mailbox=mailbox,
            fingerprint_len=len(fingerprint),
        )
        return assessed_urgency

    same_thread = await count_automated_inbound_48h(session, thread_id, now=clock)
    members, grain = await _open_cluster(session, thread, now=clock)
    if grain < same_thread:
        grain = same_thread
    floor = recurrence_urgency_floor(
        automated_inbound_count_48h=grain,
        is_finished=False,
    )
    member_max = assessed_urgency
    for row in members:
        if row.id == thread_id:
            continue
        member_max = max_urgency(member_max, row.urgency)
    applied = max_urgency(assessed_urgency, floor)
    applied = max_urgency(applied, member_max)
    if applied is None:
        return assessed_urgency

    pending = [row for row in members if row.urgency != applied]
    if not pending:
        return applied

    reason = _bump_reason(grain, applied)
    cluster_ids = [str(row.id) for row in members]
    scope = "same_thread" if len(members) <= 1 else "cross_thread"
    logger.info(
        "recurrence_escalation",
        thread_id=str(thread_id),
        mailbox=mailbox,
        fingerprint_len=len(fingerprint) if fingerprint else 0,
        grain=grain,
        same_thread=same_thread,
        cluster_size=len(members),
        applied=applied,
        scope=scope,
    )
    for row in pending:
        previous = assessed_urgency if row.id == thread_id else row.urgency
        await _apply_to_thread(
            session,
            thread_id=row.id,
            conversation_id=row.conversation_id,
            mailbox=row.mailbox,
            applied=applied,
            reason=reason,
            previous=previous,
            floor=floor,
            count=grain,
            cluster_ids=cluster_ids,
            scope=scope,
        )
    return applied


async def apply_urgency_feedback(
    session: AsyncSession,
    *,
    thread_id: uuid.UUID,
    action: str,
    actor: str,
    note: str | None = None,
) -> str | None:
    """Revert this thread's auto-bump and suppress the fingerprint."""
    if action != "wrong_escalation":
        raise ValueError(f"Unsupported urgency feedback action: {action}")
    thread = await thread_repo.get_by_id(session, thread_id)
    if thread is None:
        return None
    events = await audit_repo.list_raw_by_conversation(
        session, thread.conversation_id, mailbox=thread.mailbox
    )
    previous = None
    for event in events:
        if event.get("event_type") != _ESCALATED:
            continue
        payload = event.get("payload") or {}
        candidate = payload.get("previous")
        if isinstance(candidate, str) and candidate:
            previous = candidate
    if not isinstance(previous, str) or not previous:
        raise ThreadStateError("No automatic urgency bump to reverse")
    reverted = previous
    reason = "Automatic urgency bump marked wrong."
    await thread_repo.set_urgency(
        session,
        thread_id,
        urgency=reverted,
        urgency_reason=reason,
    )
    draft = await draft_repo.get_latest_by_thread(session, thread_id)
    if draft is not None:
        await draft_repo.set_urgency(
            session,
            draft.id,
            urgency=reverted,
            urgency_reason=reason,
        )
    fingerprint = thread.alert_fingerprint
    if fingerprint:
        await alert_fingerprint_feedback_repo.insert_suppress(
            session,
            mailbox=thread.mailbox,
            fingerprint=fingerprint,
            source_thread_id=thread.id,
            actor=actor,
            previous_urgency=thread.urgency,
            assessed_urgency=previous if isinstance(previous, str) else None,
            note=note,
        )
    await audit_service.log_event(
        session,
        event_type=_WRONG,
        conversation_id=thread.conversation_id,
        mailbox=thread.mailbox,
        payload={
            "previous": thread.urgency,
            "new": reverted,
            "human": {
                "title": "Automatic urgency bump marked wrong",
                "body": (
                    "Reverted this thread. This alert fingerprint will not auto-bump again."
                ),
                "actor_kind": "elise",
            },
        },
        actor=actor,
    )
    return reverted


def recurrence_hint(count_48h: int, *, similar_thread_count: int | None = None) -> str | None:
    grain = count_48h
    if similar_thread_count is not None:
        grain = max(grain, similar_thread_count)
    floor = recurrence_urgency_floor(automated_inbound_count_48h=grain)
    if floor is None:
        return None
    if similar_thread_count and similar_thread_count > 1:
        return (
            f"{similar_thread_count} similar automated alerts in 48h "
            f"(same sender and subject). Urgency should be at least {floor}."
        )
    return (
        f"Same-thread recurring automated alert: {count_48h} in 48h. "
        f"Urgency should be at least {floor}."
    )
