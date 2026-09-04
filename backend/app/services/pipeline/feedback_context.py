"""Feedback-loop extras for the draft pipeline: thread facts, associations, pairs."""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from anthropic import AsyncAnthropic
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models.schemas.email import EmailMessageSchema, ThreadContextSchema

logger = structlog.get_logger(__name__)


async def extract_thread_context_safe(
    *,
    thread_id: uuid.UUID,
    client: AsyncAnthropic,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    source: str = "pipeline",
) -> None:
    if not settings.thread_context_enabled:
        return
    from app.repositories import message_repo, thread_context_repo
    from app.services import thread_context_service

    try:
        async with session_factory() as session, session.begin():
            messages = await message_repo.list_by_thread(session, thread_id)
            pointer = await thread_context_repo.get(session, thread_id)
            if pointer is None and len(messages) <= settings.thread_full_if_at_most:
                return
            await thread_context_service.extract_if_needed(
                session,
                thread_id,
                client=client,
                settings=settings,
                source=source,
            )
    except Exception:
        logger.exception("pipeline_thread_context_failed", thread_id=str(thread_id))


async def load_draft_side_context(
    session: AsyncSession,
    *,
    thread_id: uuid.UUID,
    settings: Settings,
    mailbox: str,
    thread_context: ThreadContextSchema,
    current_email: EmailMessageSchema,
) -> tuple[list[Any], dict[str, str] | None, dict[str, Any]]:
    """Confirmed associations, salute directory, and per-thread working memory."""
    from app.core.internal_mail import extract_email_address
    from app.services import related_thread_service, thread_context_service

    confirmed = await related_thread_service.load_confirmed_contexts(session, thread_id)
    directory: dict[str, str] | None = None
    if settings.salute_directory_enabled:
        from app.services import directory_lookup_service

        directory = await directory_lookup_service.build_directory(
            session,
            mailbox,
            thread_context,
            current=current_email,
        )
    working_memory = await thread_context_service.load_draft_working_memory(
        session,
        thread_id,
        settings=settings,
        mailbox=mailbox,
        recipient=extract_email_address(current_email.sender),
    )
    return confirmed, directory, working_memory


async def load_paired_draft_pack(
    session: AsyncSession,
    *,
    settings: Settings,
    openai_client: object,
    anthropic_client: AsyncAnthropic,
    mailbox: str,
    email_text: str,
    sender: str,
    routing_category: str,
    negative_constraints: list[str],
) -> tuple[list[str], list[uuid.UUID], list[uuid.UUID], list[dict], list[dict[str, str | None]]]:
    """Merge paired retrieval onto legacy constraints. Returns pack fields for the draft phase."""
    from app.services.feedback_draft_context import load_paired_constraints

    sender_addr = (sender or "").strip()
    sender_dom = sender_addr.split("@")[-1] if "@" in sender_addr else ""
    paired_ctx = await load_paired_constraints(
        session,
        settings=settings,
        openai_client=openai_client,
        anthropic_client=anthropic_client,
        mailbox=mailbox,
        email_text=email_text,
        sender_address=sender_addr or None,
        sender_domain=sender_dom or None,
        routing_category=routing_category,
    )
    merged = negative_constraints
    if paired_ctx.fix_constraints:
        merged = [*paired_ctx.fix_constraints, *negative_constraints]
    paired_examples = [
        {
            "chosen": block.chosen,
            "rejected": block.rejected,
            "scope_source": block.scope_source,
        }
        for block in paired_ctx.pair_blocks
    ]
    return (
        merged,
        paired_ctx.atom_ids,
        paired_ctx.note_ids,
        list(paired_ctx.fix_atom_payloads),
        paired_examples,
    )


async def append_recurrence_hint(
    session: AsyncSession,
    *,
    email: EmailMessageSchema,
    thread_id: uuid.UUID,
    urgency_hints: list[str],
) -> list[str]:
    from app.core.tenant_scope import TenantScope
    from app.repositories import thread_repo
    from app.services import recurrence_service

    try:
        count = await recurrence_service.count_automated_inbound_48h(session, thread_id)
        similar = None
        thread_row = await thread_repo.get_by_id(
            session, thread_id, TenantScope.single(email.mailbox)
        )
        if thread_row is not None and thread_row.alert_fingerprint:
            from datetime import UTC, datetime

            cluster = await thread_repo.list_open_alert_cluster(
                session,
                mailbox=thread_row.mailbox,
                fingerprint=thread_row.alert_fingerprint,
                signature=thread_row.alert_signature,
                sender_norm=thread_row.alert_sender_norm,
                now=datetime.now(UTC),
            )
            similar = len(cluster) if cluster else None
        hint = recurrence_service.recurrence_hint(count, similar_thread_count=similar)
        if hint:
            return [hint, *urgency_hints]
    except Exception:
        logger.warning("recurrence_hint_failed", thread_id=str(thread_id))
    return urgency_hints
