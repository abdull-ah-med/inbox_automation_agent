"""Draft repository — async CRUD returning Pydantic schemas."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal, cast

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.draft import Draft
from app.models.db.thread import Thread
from app.models.schemas.draft import (
    AppliedSkillSchema,
    DraftResponseSchema,
    DraftSchema,
    DraftToolCallSchema,
    SuggestedActionSchema,
    SuggestedRecipientSchema,
)
from app.repositories._vector_common import cap_limit

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


def _parse_applied_skills(raw: object) -> list[AppliedSkillSchema]:
    if not isinstance(raw, list):
        return []
    out: list[AppliedSkillSchema] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        skill_id = item.get("id")
        name = item.get("name")
        if skill_id is None or not isinstance(name, str) or not name.strip():
            continue
        try:
            out.append(AppliedSkillSchema(id=uuid.UUID(str(skill_id)), name=name.strip()))
        except (TypeError, ValueError):
            continue
    return out


def _parse_tool_calls(raw: object) -> list[DraftToolCallSchema] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        return None
    out: list[DraftToolCallSchema] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        skill_id = item.get("skill_id")
        path = item.get("path")
        if not isinstance(skill_id, str) or not isinstance(path, str):
            continue
        out.append(
            DraftToolCallSchema(
                skill_id=skill_id,
                path=path,
                is_error=bool(item.get("is_error")),
                bytes=item.get("bytes") if isinstance(item.get("bytes"), int) else None,
                truncated=(
                    item.get("truncated") if isinstance(item.get("truncated"), bool) else None
                ),
                iteration=(
                    item.get("iteration") if isinstance(item.get("iteration"), int) else None
                ),
            )
        )
    return out


def _applied_skills_payload(
    applied_skills: list[AppliedSkillSchema] | list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    if not applied_skills:
        return None
    out: list[dict[str, Any]] = []
    for item in applied_skills:
        if isinstance(item, AppliedSkillSchema):
            out.append({"id": str(item.id), "name": item.name})
            continue
        if isinstance(item, dict):
            skill_id = item.get("id")
            name = item.get("name")
            if skill_id is None or not isinstance(name, str) or not name.strip():
                continue
            out.append({"id": str(skill_id), "name": name.strip()})
    return out or None


def _to_response(row: Draft) -> DraftResponseSchema:
    suggested, forward_to = _parse_recipients(row.recipients)
    urgency_raw = row.urgency if row.urgency in _VALID_URGENCY else "NORMAL"
    feedback_reason = row.feedback_reason_code
    routing = row.routing_category
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
        feedback_note=row.feedback_note if isinstance(row.feedback_note, str) else None,
        feedback_action=row.feedback_action if isinstance(row.feedback_action, str) else None,
        feedback_reason_code=feedback_reason if isinstance(feedback_reason, str) else None,
        routing_category=routing if isinstance(routing, str) else None,
        approval_note=row.approval_note if isinstance(row.approval_note, str) else None,
        approval_scope=row.approval_scope if isinstance(row.approval_scope, str) else None,
        suggested_actions=_parse_suggested_actions(row.suggested_actions),
        applied_skills=_parse_applied_skills(row.applied_skills_json),
        tool_calls=_parse_tool_calls(row.tool_calls_json),
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
    routing_category: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    applied_skills: list[AppliedSkillSchema] | list[dict[str, Any]] | None = None,
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
        routing_category=routing_category,
        tool_calls_json=tool_calls,
        applied_skills_json=_applied_skills_payload(applied_skills),
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
    tool_calls: list[dict[str, Any]] | None = None,
    applied_skills: list[AppliedSkillSchema] | list[dict[str, Any]] | None = None,
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
        tool_calls_json=tool_calls,
        applied_skills_json=_applied_skills_payload(applied_skills),
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
    approval_note: str | None = None,
    approval_scope: str | None = None,
) -> DraftResponseSchema | None:
    existing = await get_draft_by_id(session, draft_id)
    if existing is None:
        return None

    already_approved = existing.approved_at is not None and existing.feedback_action == "approve"
    current_body = existing.edited_body or existing.reply_body
    learning_unchanged = (
        approval_note == existing.approval_note and approval_scope == existing.approval_scope
    )
    if (
        already_approved
        and (edited_body is None or edited_body == current_body)
        and learning_unchanged
    ):
        return existing

    values: dict[str, Any] = {
        "feedback_action": "approve",
        "rejected_at": None,
    }
    if not already_approved:
        values["approved_at"] = datetime.now(UTC)
    if edited_body is not None:
        values["edited_body"] = edited_body
    if approval_note is not None or approval_scope is not None:
        values["approval_note"] = approval_note
        values["approval_scope"] = approval_scope
        values["approval_note_persisted_at"] = datetime.now(UTC)

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
    reason_code: str,
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
            feedback_reason_code=reason_code,
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
    reason_code: str,
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
            feedback_reason_code=reason_code,
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


async def set_urgency(
    session: AsyncSession,
    draft_id: uuid.UUID,
    *,
    urgency: str,
    urgency_reason: str,
) -> DraftResponseSchema | None:
    """Update draft urgency + reason."""
    stmt = (
        update(Draft)
        .where(Draft.id == draft_id)
        .values(urgency=urgency, urgency_reason=urgency_reason)
        .returning(Draft)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    await session.flush()
    return _to_response(row)


async def count_approvals(
    session: AsyncSession,
    *,
    mailbox: str,
    routing_category: str | None = None,
    since: datetime | None = None,
) -> int:
    """Count approved drafts for a mailbox (optional category + since filter)."""
    stmt = (
        select(func.count())
        .select_from(Draft)
        .join(Thread, Thread.id == Draft.thread_id)
        .where(
            Thread.mailbox == mailbox,
            Draft.approved_at.is_not(None),
            Draft.feedback_action == "approve",
        )
    )
    if routing_category is not None:
        stmt = stmt.where(Draft.routing_category == routing_category)
    if since is not None:
        stmt = stmt.where(Draft.approved_at > since)
    result = await session.execute(stmt)
    return int(result.scalar_one())


async def list_recent_approved_bodies(
    session: AsyncSession,
    *,
    mailbox: str,
    routing_category: str | None = None,
    limit: int = 20,
) -> list[str]:
    """Return recent approved reply bodies (edited_body ?? body), newest first."""
    capped = cap_limit(limit, maximum=50)
    stmt = (
        select(Draft.edited_body, Draft.body)
        .join(Thread, Thread.id == Draft.thread_id)
        .where(
            Thread.mailbox == mailbox,
            Draft.approved_at.is_not(None),
            Draft.feedback_action == "approve",
        )
        .order_by(Draft.approved_at.desc())
        .limit(capped)
    )
    if routing_category is not None:
        stmt = stmt.where(Draft.routing_category == routing_category)
    result = await session.execute(stmt)
    bodies: list[str] = []
    for edited, body in result.all():
        text = (edited or body or "").strip()
        if text:
            bodies.append(text)
    return bodies


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


async def review_finished_by_threads(
    session: AsyncSession,
    thread_ids: list[uuid.UUID],
) -> set[uuid.UUID]:
    """Thread ids whose latest draft is approved or marked wrong (no-reply)."""
    if not thread_ids:
        return set()
    ranked = (
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
        .where(Draft.thread_id.in_(thread_ids))
        .subquery()
    )
    stmt = select(ranked.c.tid).where(
        ranked.c.rn == 1,
        or_(
            ranked.c.approved_at.is_not(None),
            ranked.c.feedback_action == "wrong",
        ),
    )
    result = await session.execute(stmt)
    return {row[0] for row in result.all()}


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


async def list_by_thread(
    session: AsyncSession,
    thread_id: uuid.UUID,
) -> list[DraftResponseSchema]:
    """Return all drafts for a thread, newest first."""
    stmt = select(Draft).where(Draft.thread_id == thread_id).order_by(Draft.created_at.desc())
    result = await session.execute(stmt)
    return [_to_response(row) for row in result.scalars().all()]
