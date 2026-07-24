"""Draft repository — async CRUD returning Pydantic schemas."""

from __future__ import annotations

import uuid
from typing import Any, Literal, cast

from sqlalchemy import select
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
