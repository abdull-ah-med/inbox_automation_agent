"""Execute InboxAssistant's read-only retrieval tools. Mail.Read only."""

from __future__ import annotations

import uuid

from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import UnknownMailboxError
from app.core.mailbox_keys import resolve_mailbox_email
from app.core.sanitize import sanitize_user_text
from app.llm.chat_tools import ChatToolExecution
from app.llm.email_clean import clean_email_body
from app.llm.pii_redact import scrub_text
from app.models.schemas.search import SearchHit
from app.repositories import message_repo, thread_repo
from app.services import search_service

GET_THREAD_MAX_MESSAGES = 8
GET_THREAD_BODY_CHARS = 4000


def _tool_mailbox(arguments: dict, fallback: str | None) -> str | None:
    raw = arguments.get("mailbox")
    if isinstance(raw, str) and raw.strip():
        return sanitize_user_text(raw)
    return fallback


async def execute_chat_tool(
    session: AsyncSession,
    settings: Settings,
    *,
    openai_client: AsyncOpenAI | None,
    name: str,
    arguments: dict,
    mailbox: str | None,
    limit: int,
) -> ChatToolExecution:
    """Run one chat tool. Unknown tools and missing threads are errors, not leaks."""
    scoped = _tool_mailbox(arguments, mailbox)
    try:
        if name == "search_mail":
            query = sanitize_user_text(str(arguments.get("query") or ""))
            search = await search_service.search_threads(
                session,
                settings,
                openai_client=openai_client,
                query=query,
                mailbox=scoped,
                limit=limit,
                mode="hybrid",
                allow_empty=True,
            )
            return ChatToolExecution(hits=search.hits, status="Searching mail")
        if name == "list_recent_threads":
            mailboxes = list(settings.mailbox_list)
            if scoped:
                resolved = _scope_email(settings, scoped)
                mailboxes = [resolved] if resolved else []
            rows = await thread_repo.list_needs_attention(
                session,
                mailboxes,
                limit=limit,
            )
            hits = [
                SearchHit(
                    thread_id=row.id,
                    mailbox=row.mailbox,
                    conversation_id="",
                    subject=row.subject,
                    state=row.state,
                    urgency=row.urgency,
                    snippet=row.preview or "",
                    score=1.0,
                    last_message_at=row.last_message_at,
                )
                for row in rows
            ]
            return ChatToolExecution(hits=hits, status="Listing recent threads")
        if name == "get_thread":
            return await _get_thread(
                session,
                settings,
                thread_id_raw=str(arguments.get("thread_id") or ""),
                mailbox=scoped,
            )
    except UnknownMailboxError:
        return ChatToolExecution(
            hits=[],
            status="Searching mail",
            error="Mailbox not found",
        )
    return ChatToolExecution(hits=[], status="", error=f"Unknown tool {name}")


async def _get_thread(
    session: AsyncSession,
    settings: Settings,
    *,
    thread_id_raw: str,
    mailbox: str | None,
) -> ChatToolExecution:
    try:
        thread_id = uuid.UUID(thread_id_raw.strip())
    except ValueError:
        return ChatToolExecution(
            hits=[],
            status="Opening thread",
            error="Thread not found",
        )
    thread = await thread_repo.get_by_id(session, thread_id)
    if thread is None or not settings.mailbox_allowed(thread.mailbox):
        return ChatToolExecution(
            hits=[],
            status="Opening thread",
            error="Thread not found",
        )
    if mailbox:
        try:
            scoped = _scope_email(settings, mailbox)
        except UnknownMailboxError:
            return ChatToolExecution(
                hits=[],
                status="Opening thread",
                error="Thread not found",
            )
        if scoped is not None and thread.mailbox != scoped:
            return ChatToolExecution(
                hits=[],
                status="Opening thread",
                error="Thread not found",
            )
    messages = await message_repo.list_by_thread(session, thread_id)
    tail = messages[-GET_THREAD_MAX_MESSAGES:]
    parts: list[str] = []
    for message in tail:
        raw = message.body_text or message.body_preview or ""
        content_type = "html" if "<" in raw else "text"
        cleaned = clean_email_body(raw, content_type=content_type).body_clean
        cleaned = scrub_text(cleaned)
        sender = scrub_text(message.sender or "")
        when = message.received_at.isoformat() if message.received_at else ""
        parts.append(f"From: {sender} ({when})\n{cleaned}")
    body = "\n\n".join(parts).strip()
    if len(body) > GET_THREAD_BODY_CHARS:
        body = body[:GET_THREAD_BODY_CHARS].rstrip() + "..."
    hit = SearchHit(
        thread_id=thread.id,
        mailbox=thread.mailbox,
        conversation_id=thread.conversation_id,
        subject=thread.subject or None,
        state=thread.state,
        urgency=thread.urgency,
        snippet=body,
        score=1.0,
        last_message_at=thread.last_message_at,
    )
    return ChatToolExecution(hits=[hit], status="Opening thread")


def _scope_email(settings: Settings, mailbox: str) -> str | None:
    email = resolve_mailbox_email(sanitize_user_text(mailbox), list(settings.mailbox_list))
    if email is None or not settings.mailbox_allowed(email):
        raise UnknownMailboxError("Mailbox not found")
    return email
