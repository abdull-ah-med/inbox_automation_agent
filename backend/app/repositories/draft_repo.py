"""Draft repository — async CRUD returning Pydantic schemas."""

from __future__ import annotations

import uuid
from typing import Any, Literal, cast

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.draft import Draft
from app.models.schemas.draft import (
    DraftResponseSchema,
    DraftSchema,
    SuggestedRecipientSchema,
)

Urgency = Literal["CRITICAL", "HIGH", "NORMAL", "LOW"]
_VALID_URGENCY = frozenset({"CRITICAL", "HIGH", "NORMAL", "LOW"})


def _recipients_payload(draft: DraftSchema) -> dict[str, Any]:
    return {
        "suggested_recipients": [
            {"role": item.role, "rationale": item.rationale} for item in draft.suggested_recipients
        ],
        "forward_to": draft.forward_to,
    }


def _parse_recipients(
    recipients: dict[str, Any] | None,
) -> tuple[list[SuggestedRecipientSchema], str | None]:
    raw = recipients or {}
    suggested_raw = raw.get("suggested_recipients") or []
    suggested: list[SuggestedRecipientSchema] = []
    if isinstance(suggested_raw, list):
        for item in suggested_raw:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            rationale = item.get("rationale")
            if isinstance(role, str) and isinstance(rationale, str):
                suggested.append(SuggestedRecipientSchema(role=role, rationale=rationale))
    forward_to = raw.get("forward_to")
    forward = forward_to if isinstance(forward_to, str) else None
    return suggested, forward


def _to_response(row: Draft) -> DraftResponseSchema:
    suggested, forward_to = _parse_recipients(row.recipients)
    urgency_raw = row.urgency if row.urgency in _VALID_URGENCY else "NORMAL"
    return DraftResponseSchema(
        id=row.id,
        thread_id=row.thread_id,
        created_at=row.created_at,
        approved_at=row.approved_at,
        rejected_at=row.rejected_at,
        edited_body=row.edited_body,
        subject_line=row.subject,
        reply_body=row.body,
        suggested_recipients=suggested,
        forward_to=forward_to,
        teaching_note=row.teaching_note,
        urgency=cast(Urgency, urgency_raw),
        urgency_reason=row.urgency_reason or "",
        context_match_confidence=row.context_match_confidence,
    )


async def get_draft_by_message(
    session: AsyncSession,
    *,
    message_id: str,
) -> DraftResponseSchema | None:
    stmt = select(Draft).where(Draft.message_id == message_id)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return _to_response(row)


async def create_draft(
    session: AsyncSession,
    *,
    thread_id: uuid.UUID,
    message_id: str,
    draft: DraftSchema,
    prompt_version: str,
    context_match_confidence: float | None = None,
) -> DraftResponseSchema:
    """Insert a draft or return the existing row on ``message_id`` conflict.

    ``prompt_version`` is accepted for callers/audit; not stored on the drafts row.
    ``context_match_confidence`` is retrieval similarity when Flow B matched a thread.
    """
    _ = prompt_version

    insert_stmt = insert(Draft).values(
        thread_id=thread_id,
        message_id=message_id,
        subject=draft.subject_line,
        body=draft.reply_body,
        recipients=_recipients_payload(draft),
        teaching_note=draft.teaching_note,
        urgency=draft.urgency,
        urgency_reason=draft.urgency_reason,
        context_match_confidence=context_match_confidence,
    )
    upsert_stmt = insert_stmt.on_conflict_do_nothing(
        index_elements=["message_id"],
    ).returning(Draft)
    result = await session.execute(upsert_stmt)
    row = result.scalar_one_or_none()
    if row is not None:
        await session.flush()
        return _to_response(row)

    existing = await get_draft_by_message(session, message_id=message_id)
    if existing is None:
        raise RuntimeError(f"Draft insert conflicted but row missing for message_id={message_id}")
    return existing


async def latest_teaching_notes_by_threads(
    session: AsyncSession,
    thread_ids: list[uuid.UUID],
) -> dict[uuid.UUID, str]:
    """Bulk-fetch each thread's most recent non-empty teaching note.

    Powers the Teaching Note preview on thread list/cards without an N+1 —
    one query for every thread on the page, keyed by ``thread_id``.
    """
    if not thread_ids:
        return {}
    latest_id = (
        select(Draft.thread_id, func.max(Draft.created_at).label("max_created_at"))
        .where(Draft.thread_id.in_(thread_ids), Draft.teaching_note.is_not(None))
        .group_by(Draft.thread_id)
        .subquery()
    )
    stmt = select(Draft.thread_id, Draft.teaching_note).join(
        latest_id,
        (Draft.thread_id == latest_id.c.thread_id)
        & (Draft.created_at == latest_id.c.max_created_at),
    )
    result = await session.execute(stmt)
    return {row.thread_id: row.teaching_note for row in result if row.teaching_note}


async def get_latest_by_thread(
    session: AsyncSession,
    thread_id: uuid.UUID,
) -> DraftResponseSchema | None:
    """Return the most recent draft for a thread, if any."""
    stmt = (
        select(Draft)
        .where(Draft.thread_id == thread_id)
        .order_by(Draft.created_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return _to_response(row)
