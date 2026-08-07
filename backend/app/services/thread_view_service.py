"""Assemble thread detail views for the web UI."""

from __future__ import annotations

import difflib
import uuid
from typing import Literal

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.outlook_links import outlook_web_link
from app.models.schemas.dashboard import (
    AppliedSkillView,
    DraftToolCallView,
    DraftView,
    DraftVsSentDiff,
    MessageDetail,
    SentReplyView,
    SuggestedActionView,
    ThreadDetail,
)
from app.models.schemas.draft import DraftResponseSchema
from app.repositories import (
    audit_repo,
    classification_repo,
    draft_repo,
    message_repo,
    sent_reply_repo,
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
        approval_note=draft.approval_note,
        approval_scope=draft.approval_scope,
        applied_skills=[
            AppliedSkillView(id=skill.id, name=skill.name) for skill in draft.applied_skills
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


def _proposed_body(draft: DraftView | None) -> str | None:
    if draft is None:
        return None
    if draft.edited_body and draft.edited_body.strip():
        return draft.edited_body
    return draft.body


def compute_draft_vs_sent_diff(
    proposed: str | None,
    sent: str | None,
) -> DraftVsSentDiff | None:
    """Line-level added/removed sets between proposed draft and sent body."""
    if proposed is None or sent is None:
        return None
    proposed_lines = proposed.splitlines()
    sent_lines = sent.splitlines()
    added: list[str] = []
    removed: list[str] = []
    for line in difflib.unified_diff(
        proposed_lines,
        sent_lines,
        lineterm="",
        n=0,
    ):
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        if line.startswith("+"):
            added.append(line[1:])
        elif line.startswith("-"):
            removed.append(line[1:])
    return DraftVsSentDiff(added=added, removed=removed)


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

    sent_row = await sent_reply_repo.get_by_thread(session, thread_id)
    sent_reply: SentReplyView | None = None
    draft_vs_sent_diff: DraftVsSentDiff | None = None
    if sent_row is not None:
        matched_by: Literal["approved_draft", "time_window", "manual"]
        if sent_row.matched_by == "approved_draft":
            matched_by = "approved_draft"
        elif sent_row.matched_by == "manual":
            matched_by = "manual"
        else:
            matched_by = "time_window"
        sent_reply = SentReplyView(
            id=sent_row.id,
            thread_id=sent_row.thread_id,
            message_id=sent_row.message_id,
            draft_id=sent_row.draft_id,
            sent_body_snapshot=sent_row.sent_body_snapshot,
            sent_at=sent_row.sent_at,
            matched_by=matched_by,
            created_at=sent_row.created_at,
        )
        draft_vs_sent_diff = compute_draft_vs_sent_diff(
            _proposed_body(draft),
            sent_row.sent_body_snapshot,
        )

    return ThreadDetail(
        thread=summary,
        messages=message_details,
        classification=classification,
        draft=draft,
        triage=triage,
        audit_log=audit_log,
        sent_reply=sent_reply,
        draft_vs_sent_diff=draft_vs_sent_diff,
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
