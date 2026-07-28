"""Assemble thread detail views for the web UI."""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.outlook_links import outlook_web_link
from app.models.schemas.dashboard import (
    DraftView,
    MessageDetail,
    ThreadDetail,
)
from app.repositories import (
    audit_repo,
    classification_repo,
    draft_repo,
    message_repo,
    thread_repo,
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
        draft = DraftView(
            id=draft_row.id,
            subject=draft_row.subject_line,
            body=draft_row.edited_body or draft_row.reply_body,
            teaching_note=draft_row.teaching_note,
            urgency=draft_row.urgency,
            urgency_reason=draft_row.urgency_reason,
            forward_to=draft_row.forward_to,
            created_at=draft_row.created_at,
        )
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
