"""Assemble thread detail views for the web UI."""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.outlook_links import outlook_web_link
from app.models.schemas.dashboard import (
    AppliedSkillView,
    DraftToolCallView,
    DraftView,
    MessageDetail,
    SuggestedActionView,
    ThreadDetail,
)
from app.models.schemas.draft import DraftResponseSchema
from app.repositories import (
    audit_repo,
    classification_repo,
    draft_repo,
    message_repo,
    thread_repo,
)


def draft_response_to_view(draft: DraftResponseSchema) -> DraftView:
    """Map internal draft schema to the dashboard/API DraftView contract."""
    return DraftView(
        id=draft.id,
        subject=draft.subject_line,
        body=draft.edited_body or draft.reply_body,
        teaching_note=draft.teaching_note,
        urgency=draft.urgency,
        urgency_reason=draft.urgency_reason,
        forward_to=draft.forward_to,
        created_at=draft.created_at,
        suggested_actions=[
            SuggestedActionView(
                step=action.step,
                action=action.action,
                stakeholder=action.stakeholder,
                rationale=action.rationale,
            )
            for action in draft.suggested_actions
        ],
        approved_at=draft.approved_at,
        rejected_at=draft.rejected_at,
        edited_body=draft.edited_body,
        feedback_note=draft.feedback_note,
        feedback_action=draft.feedback_action,
        feedback_reason_code=draft.feedback_reason_code,
        routing_category=draft.routing_category,
        applied_skills=[
            AppliedSkillView(id=skill.id, name=skill.name)
            for skill in draft.applied_skills
        ],
        tool_calls=(
            [
                DraftToolCallView(
                    skill_id=call.skill_id,
                    path=call.path,
                    is_error=call.is_error,
                )
                for call in draft.tool_calls
            ]
            if draft.tool_calls is not None
            else None
        ),
    )


async def get_thread_detail(
    session: AsyncSession,
    settings: Settings,
    thread_id: uuid.UUID,
) -> ThreadDetail:
    thread = await thread_repo.get_by_id(session, thread_id)
    if thread is None or not settings.mailbox_allowed(thread.mailbox):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")

    summary = await thread_repo.build_thread_summary(session, thread)
    messages = await message_repo.list_by_thread(session, thread_id)
    message_details = [
        MessageDetail(
            id=m.id,
            direction=m.direction,
            sender=m.sender,
            to=list(m.to_recipients),
            cc=list(m.cc_recipients),
            body_text=m.body_text,
            body_preview=m.body_preview,
            received_at=m.received_at,
            has_attachments=bool(m.has_attachments),
            outlook_url=outlook_web_link(m.graph_message_id),
        )
        for m in messages
    ]
    classification = await classification_repo.get_latest_for_thread(session, thread_id)
    draft_row = await draft_repo.get_latest_by_thread(session, thread_id)
    draft: DraftView | None = None
    if draft_row is not None:
        draft = draft_response_to_view(draft_row)
    audit_log = await audit_repo.list_by_thread_id(
        session,
        thread_id,
        thread.conversation_id,
        mailbox=thread.mailbox,
    )
    triage = summary.triage or await audit_repo.get_latest_triage_flags(
        session,
        thread.conversation_id,
        mailbox=thread.mailbox,
    )
    return ThreadDetail(
        thread=summary,
        messages=message_details,
        classification=classification,
        draft=draft,
        triage=triage,
        audit_log=audit_log,
    )


async def list_thread_messages(
    session: AsyncSession,
    settings: Settings,
    thread_id: uuid.UUID,
) -> list[MessageDetail]:
    thread = await thread_repo.get_by_id(session, thread_id)
    if thread is None or not settings.mailbox_allowed(thread.mailbox):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")

    messages = await message_repo.list_by_thread(session, thread_id)
    return [
        MessageDetail(
            id=m.id,
            direction=m.direction,
            sender=m.sender,
            to=list(m.to_recipients),
            cc=list(m.cc_recipients),
            body_text=m.body_text,
            body_preview=m.body_preview,
            received_at=m.received_at,
            has_attachments=bool(m.has_attachments),
            outlook_url=outlook_web_link(m.graph_message_id),
        )
        for m in messages
    ]
