"""Audit event repository — append-only inserts into ``audit_events``."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.audit_event import AuditEvent
from app.models.schemas.audit import AuditEventSchema
from app.models.schemas.dashboard import AuditEntry, TriageFlags

_TRIAGE_EVENTS = (
    "triage.action_needed",
    "triage.no_action_discarded",
    "triage.spam_discarded",
    "triage.failed",
)


def _payload_bool(payload: dict[str, Any], key: str) -> bool | None:
    value = payload.get(key)
    if isinstance(value, bool):
        return value
    return None


def _payload_str(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value
    return None


def triage_flags_from_event(event: AuditEvent) -> TriageFlags:
    payload = event.payload if isinstance(event.payload, dict) else {}
    return TriageFlags(
        is_spam=_payload_bool(payload, "is_spam"),
        has_action_items=_payload_bool(payload, "has_action_items"),
        needs_context=_payload_bool(payload, "needs_context"),
        spam_reason=_payload_str(payload, "spam_reason"),
        context_reason=_payload_str(payload, "context_reason"),
        action_items_summary=_payload_str(payload, "action_items_summary"),
        routing_category=_payload_str(payload, "routing_category"),
        outcome=event.event_type,
    )


async def get_latest_triage_flags(
    session: AsyncSession,
    conversation_id: str,
    *,
    mailbox: str,
) -> TriageFlags | None:
    stmt = (
        select(AuditEvent)
        .where(
            AuditEvent.conversation_id == conversation_id,
            AuditEvent.mailbox == mailbox,
            AuditEvent.event_type.in_(_TRIAGE_EVENTS),
        )
        .order_by(AuditEvent.created_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    event = result.scalar_one_or_none()
    if event is None:
        return None
    return triage_flags_from_event(event)


async def triage_flags_by_conversations(
    session: AsyncSession,
    pairs: list[tuple[str, str]],
) -> dict[tuple[str, str], TriageFlags]:
    """Return latest triage flags keyed by ``(mailbox, conversation_id)``."""
    if not pairs:
        return {}
    mailboxes = sorted({mailbox for mailbox, _ in pairs})
    conversation_ids = sorted({cid for _, cid in pairs})
    stmt = (
        select(AuditEvent)
        .where(
            AuditEvent.mailbox.in_(mailboxes),
            AuditEvent.conversation_id.in_(conversation_ids),
            AuditEvent.event_type.in_(_TRIAGE_EVENTS),
        )
        .order_by(
            AuditEvent.mailbox,
            AuditEvent.conversation_id,
            AuditEvent.created_at.desc(),
        )
    )
    result = await session.execute(stmt)
    wanted = set(pairs)
    out: dict[tuple[str, str], TriageFlags] = {}
    for event in result.scalars().all():
        key = (event.mailbox, event.conversation_id)
        if key not in wanted or key in out:
            continue
        out[key] = triage_flags_from_event(event)
    return out


async def create_audit_event(
    session: AsyncSession,
    *,
    event_type: str,
    conversation_id: str,
    mailbox: str,
    payload: dict[str, Any] | None = None,
    actor: str = "system",
) -> AuditEventSchema:
    """Insert one audit row and return the Pydantic schema (no commit)."""
    event = AuditEvent(
        event_type=event_type,
        conversation_id=conversation_id,
        mailbox=mailbox,
        payload=dict(payload or {}),
        actor=actor,
    )
    session.add(event)
    await session.flush()
    await session.refresh(event)
    return AuditEventSchema.model_validate(event)


def _to_entry(event: AuditEvent) -> AuditEntry:
    detail = ""
    if isinstance(event.payload, dict):
        detail = str(event.payload.get("detail") or event.payload.get("summary") or "")
        if not detail and event.payload:
            detail = ", ".join(f"{k}={v}" for k, v in list(event.payload.items())[:4])
    source = event.actor if event.actor in {"system", "agent", "elise"} else "system"
    return AuditEntry(
        timestamp=event.created_at,
        event=event.event_type,
        detail=detail,
        source=source,
    )


async def list_recent(
    session: AsyncSession,
    *,
    mailboxes: list[str] | None = None,
    limit: int = 25,
) -> list[AuditEntry]:
    stmt = select(AuditEvent)
    if mailboxes is not None:
        if not mailboxes:
            return []
        stmt = stmt.where(AuditEvent.mailbox.in_(mailboxes))
    stmt = stmt.order_by(AuditEvent.created_at.desc()).limit(limit)
    result = await session.execute(stmt)
    return [_to_entry(row) for row in result.scalars().all()]


async def list_by_conversation(
    session: AsyncSession,
    conversation_id: str,
    *,
    mailbox: str | None = None,
    limit: int = 100,
) -> list[AuditEntry]:
    conditions = [AuditEvent.conversation_id == conversation_id]
    if mailbox is not None:
        conditions.append(AuditEvent.mailbox == mailbox)
    stmt = select(AuditEvent).where(*conditions).order_by(AuditEvent.created_at.asc()).limit(limit)
    result = await session.execute(stmt)
    return [_to_entry(row) for row in result.scalars().all()]


async def list_by_thread_id(
    session: AsyncSession,
    thread_id: uuid.UUID,
    conversation_id: str,
    *,
    mailbox: str | None = None,
    limit: int = 100,
) -> list[AuditEntry]:
    """Audit events are keyed by conversation_id (+ mailbox when provided)."""
    _ = thread_id
    return await list_by_conversation(
        session,
        conversation_id,
        mailbox=mailbox,
        limit=limit,
    )
