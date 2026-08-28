"""Assemble thread detail views for the web UI."""

from __future__ import annotations

import difflib
import uuid
from typing import Literal

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.closing_mail import looks_like_closing_mail
from app.core.config import Settings
from app.core.email_quotes import split_quoted_history
from app.core.outlook_links import outlook_web_link
from app.core.reply_addressee import resolve_reply_addressee
from app.core.tenant_scope import TenantScope
from app.models.schemas.dashboard import (
    ActivityEntryView,
    AppliedSkillView,
    AuditEntry,
    DraftToolCallView,
    DraftView,
    DraftVsSentDiff,
    MessageDetail,
    ReplyAddresseeView,
    SentReplyView,
    SuggestedActionView,
    ThreadDetail,
    ThreadHeader,
)
from app.models.schemas.draft import DraftResponseSchema
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema, ThreadContextSchema
from app.repositories import (
    audit_repo,
    classification_repo,
    draft_repo,
    message_repo,
    sent_reply_repo,
    thread_repo,
)
from app.services import related_thread_service
from app.services.thread_narrative import build_activity

# Keep in sync with sent_reply_service meeting skip set (avoid circular import).
_MEETING_MESSAGE_TYPES = frozenset(
    {
        "meetingRequest",
        "meetingCancelled",
        "meetingAccepted",
        "meetingTenativelyAccepted",
        "meetingDeclined",
    }
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


def reply_text_for_message(
    *,
    body_text: str,
    unique_body_text: str | None,
    body_preview: str | None,
) -> str:
    """Unique new content for review. Empty string means no new text.

    Prefer stored ``unique_body_text`` (including "") over ``body_text``. Always
    quote-split so Zendesk/ticket dumps that Graph left in uniqueBody still
    show only the newest comment. NULL old rows fall back to ``body_text``,
    then preview. Display artifacts are stripped for reviewers.
    """
    from app.llm.email_clean import strip_plain_text_artifacts

    if unique_body_text is not None:
        candidate = unique_body_text
    else:
        candidate = (body_text or "").strip() or (body_preview or "")

    main, quoted = split_quoted_history(candidate or "")
    if quoted is not None:
        return strip_plain_text_artifacts(main.strip())
    raw = (candidate or "").strip()
    if not raw and unique_body_text is None:
        raw = (body_preview or "").strip()
    return strip_plain_text_artifacts(raw)


def _message_detail(m: message_repo.MessageSchema) -> MessageDetail:
    return MessageDetail(
        id=m.id,
        direction=m.direction,
        sender=m.sender,
        to=list(m.to_recipients),
        cc=list(m.cc_recipients),
        bcc=list(m.bcc_recipients),
        body_text=m.body_text,
        reply_text=reply_text_for_message(
            body_text=m.body_text,
            unique_body_text=m.unique_body_text,
            body_preview=m.body_preview,
        ),
        body_preview=m.body_preview,
        received_at=m.received_at,
        has_attachments=bool(m.has_attachments),
        outlook_url=outlook_web_link(m.graph_message_id),
        meeting_message_type=m.meeting_message_type,
        meeting_response_type=m.meeting_response_type,
    )


def _reply_only(text: str) -> str:
    """Strip Outlook/Gmail quoted history so draft vs sent compares the reply itself."""
    main, quoted = split_quoted_history(text)
    if quoted is None:
        return text
    return main if main else text


def compute_draft_vs_sent_diff(
    proposed: str | None,
    sent: str | None,
) -> DraftVsSentDiff | None:
    """Line-level added/removed sets between proposed draft and sent body.

    Quoted history is stripped from both sides first so signatures/thread quotes
    do not paint the entire proposed draft as removed.
    """
    if proposed is None or sent is None:
        return None
    proposed_lines = _reply_only(proposed).splitlines()
    sent_lines = _reply_only(sent).splitlines()
    added: list[str] = []
    removed: list[str] = []
    for line in difflib.unified_diff(
        proposed_lines,
        sent_lines,
        lineterm="",
        n=0,
    ):
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith("+"):
            added.append(line[1:])
        elif line.startswith("-"):
            removed.append(line[1:])
    return DraftVsSentDiff(added=added, removed=removed)


async def get_thread_header(
    session: AsyncSession,
    settings: Settings,
    thread_id: uuid.UUID,
) -> ThreadHeader:
    thread = await thread_repo.get_by_id(session, thread_id, TenantScope.from_settings(settings))
    if thread is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")
    return ThreadHeader(subject=thread.subject, mailbox=thread.mailbox)


async def get_thread_detail(
    session: AsyncSession,
    settings: Settings,
    thread_id: uuid.UUID,
) -> ThreadDetail:
    thread = await thread_repo.get_by_id(session, thread_id, TenantScope.from_settings(settings))
    if thread is None or not settings.mailbox_allowed(thread.mailbox):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")

    summary = await thread_repo.build_thread_summary(session, thread)
    messages = await message_repo.list_by_thread(session, thread_id)
    message_details = [_message_detail(m) for m in messages]
    classification = await classification_repo.get_latest_for_thread(session, thread_id)
    draft_row = await draft_repo.get_latest_proposed_by_thread(session, thread_id)
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
        linked = next((m for m in messages if m.id == sent_row.message_id), None)
        # Meeting accepts and empty bodies must not open the learning panel.
        meeting_linked = linked is not None and (
            (linked.meeting_message_type or "") in _MEETING_MESSAGE_TYPES
        )
        if not meeting_linked:
            sent_snapshot = sent_row.sent_body_snapshot
            if not (sent_snapshot or "").strip() and linked is not None:
                sent_snapshot = reply_text_for_message(
                    body_text=linked.body_text,
                    unique_body_text=linked.unique_body_text,
                    body_preview=linked.body_preview,
                )
            if (sent_snapshot or "").strip():
                sent_reply = SentReplyView(
                    id=sent_row.id,
                    thread_id=sent_row.thread_id,
                    message_id=sent_row.message_id,
                    draft_id=sent_row.draft_id,
                    sent_body_snapshot=sent_snapshot,
                    sent_at=sent_row.sent_at,
                    matched_by=matched_by,
                    created_at=sent_row.created_at,
                )
                draft_vs_sent_diff = compute_draft_vs_sent_diff(
                    _proposed_body(draft),
                    sent_snapshot,
                )

    associated_threads = await related_thread_service.list_stored_associations(
        session,
        thread_id,
    )

    raw_events = await audit_repo.list_raw_by_conversation(
        session,
        thread.conversation_id,
        mailbox=thread.mailbox,
    )
    narrative = build_activity(list(reversed(raw_events)))
    activity = [
        ActivityEntryView(
            title=entry.title,
            body=entry.body,
            actor_kind=entry.actor_kind,
            event_type=entry.event_type,
            timestamp=entry.timestamp,
        )
        for entry in narrative
    ]

    last_inbound_body = next(
        (m.body_text for m in reversed(messages) if m.direction == "inbound"),
        None,
    )
    closing = looks_like_closing_mail(last_inbound_body)
    draft_finished = bool(
        draft is not None and (draft.approved_at is not None or draft.feedback_action == "wrong")
    )
    reason_corrected = any(
        str(ev.get("event_type") or "") == "thread.resolved.wrong_reason" for ev in raw_events
    )
    summary = thread_repo.with_presentation(
        summary,
        draft_review_finished=draft_finished,
        closing_signal=closing,
        resolution_reason_corrected=reason_corrected,
    )

    reply_addressee = await _resolve_reply_addressee_view(
        session,
        settings,
        mailbox=thread.mailbox,
        conversation_id=thread.conversation_id,
        subject=thread.subject,
        messages=messages,
    )

    return ThreadDetail(
        thread=summary,
        messages=message_details,
        classification=classification,
        draft=draft,
        triage=triage,
        audit_log=audit_log,
        activity=activity,
        sent_reply=sent_reply,
        draft_vs_sent_diff=draft_vs_sent_diff,
        associated_threads=associated_threads,
        reply_addressee=reply_addressee,
    )


async def _resolve_reply_addressee_view(
    session: AsyncSession,
    settings: Settings,
    *,
    mailbox: str,
    conversation_id: str,
    subject: str,
    messages: list,
) -> ReplyAddresseeView | None:
    if not messages:
        return None
    schema_messages: list[EmailMessageSchema] = []
    for message in messages:
        direction = (
            EmailDirectionEnum.OUTBOUND
            if str(message.direction).lower() == "outbound"
            else EmailDirectionEnum.INBOUND
        )
        schema_messages.append(
            EmailMessageSchema(
                message_id=message.graph_message_id,
                conversation_id=conversation_id,
                mailbox=mailbox,
                sender=message.sender,
                subject=subject,
                body_text=message.body_text,
                body_preview=message.body_preview,
                received_at=message.received_at,
                direction=direction,
                to_recipients=list(message.to_recipients or []),
                cc_recipients=list(message.cc_recipients or []),
                bcc_recipients=list(message.bcc_recipients or []),
                has_attachments=bool(message.has_attachments),
            )
        )
    thread_context = ThreadContextSchema(
        conversation_id=conversation_id,
        mailbox=mailbox,
        subject=subject,
        messages=schema_messages,
    )
    directory: dict[str, str] = {}
    salute_on = settings.salute_directory_enabled
    if salute_on:
        from app.services import directory_lookup_service

        directory = await directory_lookup_service.build_directory(
            session,
            mailbox,
            thread_context,
        )
    addressee = resolve_reply_addressee(
        mailbox=mailbox,
        messages=schema_messages,
        mailbox_owner=settings.owner_for_mailbox(mailbox),
        directory=directory if salute_on else None,
        suppress_local_part=salute_on,
    )
    if addressee is None:
        return None
    return ReplyAddresseeView(
        email=addressee.email,
        salute_name=addressee.salute_name,
        source=addressee.source,
        source_kind=addressee.source_kind,
        directory_hit=addressee.directory_hit,
    )


async def list_thread_messages(
    session: AsyncSession,
    settings: Settings,
    thread_id: uuid.UUID,
) -> list[MessageDetail]:
    thread = await thread_repo.get_by_id(session, thread_id, TenantScope.from_settings(settings))
    if thread is None or not settings.mailbox_allowed(thread.mailbox):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")

    messages = await message_repo.list_by_thread(session, thread_id)
    return [_message_detail(m) for m in messages]


async def list_thread_audit(
    session: AsyncSession,
    settings: Settings,
    thread_id: uuid.UUID,
) -> list[AuditEntry]:
    thread = await thread_repo.get_by_id(session, thread_id, TenantScope.from_settings(settings))
    if thread is None or not settings.mailbox_allowed(thread.mailbox):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")
    return await audit_repo.list_by_thread_id(
        session,
        thread_id,
        thread.conversation_id,
        mailbox=thread.mailbox,
    )
