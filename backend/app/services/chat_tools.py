"""Execute InboxAssistant's read-only retrieval tools. Mail.Read only."""

from __future__ import annotations

import uuid
from contextlib import suppress
from typing import Any

from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import SearchError, UnknownMailboxError
from app.core.sanitize import sanitize_user_text
from app.core.tenant_scope import TenantScope
from app.llm.chat_tools import ChatToolExecution
from app.llm.email_clean import clean_email_body
from app.llm.pii_redact import scrub_text
from app.models.schemas.search import SearchHit
from app.repositories import message_repo, thread_repo, thread_summary_repo
from app.services import search_service
from app.services.mailbox_scope import resolve_scoped_mailboxes

GET_THREAD_MAX_MESSAGES = 8
GET_THREAD_BODY_CHARS = 4000
CHAT_HIT_MAX_MESSAGES = 4
CHAT_HIT_BODY_CHARS = 1500


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
            hits = await _enrich_hits_with_bodies(session, search.hits)
            return ChatToolExecution(hits=hits, status="Searching mail")
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
                    sender=row.last_sender,
                )
                for row in rows
            ]
            hits = await _enrich_hits_with_bodies(session, hits)
            return ChatToolExecution(hits=hits, status="Listing recent threads")
        if name == "get_thread":
            return await _get_thread(
                session,
                settings,
                thread_id_raw=str(arguments.get("thread_id") or ""),
                mailbox=scoped,
                arguments=arguments,
            )
        if name == "get_overview":
            mailboxes = list(settings.mailbox_list)
            if scoped:
                resolved = _scope_email(settings, scoped)
                mailboxes = [resolved] if resolved else []
            overview_rows = await thread_repo.aggregate_overview(session, mailboxes)
            return ChatToolExecution(
                hits=[],
                status="Summarizing mailbox",
                overview=_format_overview(overview_rows),
            )
    except UnknownMailboxError:
        return ChatToolExecution(
            hits=[],
            status="Searching mail",
            error="Mailbox not found",
        )
    except SearchError as exc:
        return ChatToolExecution(
            hits=[],
            status="Searching mail",
            error=str(exc) or "Search is temporarily unavailable",
        )
    return ChatToolExecution(hits=[], status="", error=f"Unknown tool {name}")


async def _get_thread(
    session: AsyncSession,
    settings: Settings,
    *,
    thread_id_raw: str,
    mailbox: str | None,
    arguments: dict,
) -> ChatToolExecution:
    try:
        thread_id = uuid.UUID(thread_id_raw.strip())
    except ValueError:
        return ChatToolExecution(
            hits=[],
            status="Opening thread",
            error="Thread not found",
        )
    thread = await thread_repo.get_by_id(session, thread_id, TenantScope.from_settings(settings))
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
    page = _tool_page(arguments)
    window = _message_page(messages, page=page)
    parts: list[str] = []
    if page == 0:
        summary = None
        try:
            summary = await thread_summary_repo.get(session, thread_id)
        except Exception:
            with suppress(Exception):
                await session.rollback()
            summary = None
        if summary is not None and len(messages) >= 5:
            parts.append(f"Thread summary: {summary.summary_text}")
    sender = ""
    for message in window:
        raw = message.body_text or message.body_preview or ""
        stored = (getattr(message, "body_content_type", None) or "text").strip().lower()
        content_type = stored if stored in {"html", "text"} else "text"
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
        sender=sender or None,
    )
    return ChatToolExecution(hits=[hit], status="Opening thread")


def _tool_page(arguments: dict) -> int:
    raw = arguments.get("page", 0)
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, int):
        return max(0, raw)
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    return 0


def _message_page(messages: list, *, page: int) -> list:
    if not messages:
        return []
    end = len(messages) - page * GET_THREAD_MAX_MESSAGES
    if end <= 0:
        return []
    start = max(0, end - GET_THREAD_MAX_MESSAGES)
    return messages[start:end]


def _format_overview(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No mailbox data."
    blocks: list[str] = []
    for row in rows:
        breakdown = row.get("urgency_breakdown") or {}
        urgency = (
            " ".join(f"{label}={count}" for label, count in breakdown.items() if count) or "none"
        )
        blocks.append(
            "\n".join(
                [
                    f"mailbox: {row['mailbox']}",
                    f"threads: {row['thread_count']}",
                    f"awaiting_action: {row['awaiting_action_count']}",
                    f"stale: {row['stale_count']}",
                    f"urgency: {urgency}",
                ]
            )
        )
    return "\n\n".join(blocks)


def _message_body_snippet(messages: list) -> str:
    window = messages[-CHAT_HIT_MAX_MESSAGES:] if messages else []
    parts: list[str] = []
    for message in window:
        raw = getattr(message, "body_text", None) or getattr(message, "body_preview", None) or ""
        stored = (getattr(message, "body_content_type", None) or "text").strip().lower()
        content_type = stored if stored in {"html", "text"} else "text"
        cleaned = clean_email_body(str(raw), content_type=content_type).body_clean
        cleaned = scrub_text(cleaned)
        sender = scrub_text(getattr(message, "sender", None) or "")
        received = getattr(message, "received_at", None)
        when = received.isoformat() if received else ""
        parts.append(f"From: {sender} ({when})\n{cleaned}")
    body = "\n\n".join(parts).strip()
    if len(body) > CHAT_HIT_BODY_CHARS:
        return body[:CHAT_HIT_BODY_CHARS].rstrip() + "..."
    return body


async def _enrich_hits_with_bodies(
    session: AsyncSession,
    hits: list[SearchHit],
) -> list[SearchHit]:
    if not hits:
        return []
    thread_ids = [hit.thread_id for hit in hits]
    try:
        by_thread = await message_repo.list_by_thread_ids(session, thread_ids)
    except Exception:
        with suppress(Exception):
            await session.rollback()
        return list(hits)

    enriched: list[SearchHit] = []
    for hit in hits:
        messages = by_thread.get(hit.thread_id)
        if not isinstance(messages, list) or not messages:
            enriched.append(hit)
            continue
        body = _message_body_snippet(messages)
        if not body:
            enriched.append(hit)
            continue
        enriched.append(hit.model_copy(update={"snippet": body}))
    return enriched


def _scope_email(settings: Settings, mailbox: str) -> str:
    return resolve_scoped_mailboxes(settings, mailbox)[0]
