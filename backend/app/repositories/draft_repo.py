"""Draft repository — async CRUD returning Pydantic schemas."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal, cast

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.draft import Draft
from app.models.db.thread import Thread
from app.models.schemas.draft import (
    DraftResponseSchema,
    DraftSchema,
    SuggestedActionSchema,
    SuggestedRecipientSchema,
)

Urgency = Literal["CRITICAL", "HIGH", "NORMAL", "LOW"]
_VALID_URGENCY = frozenset({"CRITICAL", "HIGH", "NORMAL", "LOW"})
FeedbackAction = Literal["approve", "reject", "wrong"]


def _recipients_payload(draft: DraftSchema) -> dict[str, Any]:
    return {
        "suggested_recipients": [
            {"role": item.role, "rationale": item.rationale} for item in draft.suggested_recipients
        ],
        "forward_to": draft.forward_to,
    }


def _suggested_actions_payload(draft: DraftSchema) -> list[dict[str, Any]]:
    return [
        {
            "step": item.step,
            "action": item.action,
            "stakeholder": item.stakeholder,
            "rationale": item.rationale,
        }
        for item in draft.suggested_actions
    ]


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


def _parse_suggested_actions(
    raw: list[Any] | dict[str, Any] | None,
) -> list[SuggestedActionSchema]:
    if not isinstance(raw, list):
        return []
    actions: list[SuggestedActionSchema] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        step = item.get("step")
        action = item.get("action")
        rationale = item.get("rationale")
        stakeholder = item.get("stakeholder")
        if (
            not isinstance(step, int)
            or not isinstance(action, str)
            or not isinstance(rationale, str)
        ):
            continue
        stakeholder_val = stakeholder if isinstance(stakeholder, str) else None
        actions.append(
            SuggestedActionSchema(
                step=step,
                action=action,
                stakeholder=stakeholder_val,
                rationale=rationale,
            )
        )
    return actions


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
        feedback_note=row.feedback_note,
        feedback_action=row.feedback_action,
        suggested_actions=_parse_suggested_actions(row.suggested_actions),
    )


async def get_draft_by_id(
    session: AsyncSession,
    draft_id: uuid.UUID,
) -> DraftResponseSchema | None:
    stmt = select(Draft).where(Draft.id == draft_id)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return _to_response(row)


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
        suggested_actions=_suggested_actions_payload(draft),
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


async def create_regenerated_draft(
    session: AsyncSession,
    *,
    thread_id: uuid.UUID,
    message_id: str,
    draft: DraftSchema,
    context_match_confidence: float | None = None,
) -> DraftResponseSchema:
    """Insert a new draft row for regeneration (unique message_id required)."""
    row = Draft(
        thread_id=thread_id,
        message_id=message_id,
        subject=draft.subject_line,
        body=draft.reply_body,
        recipients=_recipients_payload(draft),
        teaching_note=draft.teaching_note,
        urgency=draft.urgency,
        urgency_reason=draft.urgency_reason,
        context_match_confidence=context_match_confidence,
        suggested_actions=_suggested_actions_payload(draft),
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return _to_response(row)


async def approve_draft(
    session: AsyncSession,
    draft_id: uuid.UUID,
    *,
    edited_body: str | None = None,
) -> DraftResponseSchema | None:
    existing = await get_draft_by_id(session, draft_id)
    if existing is None:
        return None

    already_approved = existing.approved_at is not None and existing.feedback_action == "approve"
    current_body = existing.edited_body or existing.reply_body
    if already_approved and (edited_body is None or edited_body == current_body):
        return existing

    values: dict[str, Any] = {
        "feedback_action": "approve",
        "rejected_at": None,
    }
    if not already_approved:
        values["approved_at"] = datetime.now(UTC)
    if edited_body is not None:
        values["edited_body"] = edited_body

    stmt = update(Draft).where(Draft.id == draft_id).values(**values).returning(Draft)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    await session.flush()
    return _to_response(row)


async def reject_draft(
    session: AsyncSession,
    draft_id: uuid.UUID,
    *,
    feedback_note: str,
) -> DraftResponseSchema | None:
    existing = await get_draft_by_id(session, draft_id)
    if existing is None:
        return None
    if existing.rejected_at is not None and existing.feedback_action == "reject":
        return existing

    stmt = (
        update(Draft)
        .where(Draft.id == draft_id)
        .values(
            rejected_at=datetime.now(UTC),
            feedback_note=feedback_note,
            feedback_action="reject",
            approved_at=None,
        )
        .returning(Draft)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    await session.flush()
    return _to_response(row)


async def mark_wrong(
    session: AsyncSession,
    draft_id: uuid.UUID,
    *,
    feedback_note: str,
) -> DraftResponseSchema | None:
    existing = await get_draft_by_id(session, draft_id)
    if existing is None:
        return None
    if existing.feedback_action == "wrong":
        return existing

    stmt = (
        update(Draft)
        .where(Draft.id == draft_id)
        .values(
            feedback_note=feedback_note,
            feedback_action="wrong",
            approved_at=None,
        )
        .returning(Draft)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    await session.flush()
    return _to_response(row)


async def get_approved_drafts(
    session: AsyncSession,
    *,
    mailbox: str | None = None,
    limit: int = 100,
) -> list[DraftResponseSchema]:
    stmt = select(Draft).where(Draft.approved_at.is_not(None))
    if mailbox is not None:
        stmt = stmt.join(Thread, Thread.id == Draft.thread_id).where(Thread.mailbox == mailbox)
    stmt = stmt.order_by(Draft.approved_at.desc()).limit(limit)
    result = await session.execute(stmt)
    return [_to_response(row) for row in result.scalars().all()]


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
        select(Draft).where(Draft.thread_id == thread_id).order_by(Draft.created_at.desc()).limit(1)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return _to_response(row)
