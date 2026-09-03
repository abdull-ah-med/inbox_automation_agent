"""Workflow extraction on resolve (feedback loops v2)."""

from __future__ import annotations

import uuid

import structlog
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings

logger = structlog.get_logger(__name__)


async def maybe_extract_workflow(
    session: AsyncSession,
    *,
    client: AsyncAnthropic,
    settings: Settings,
    openai_client: AsyncOpenAI | None,
    mailbox: str,
    thread_id: uuid.UUID,
    email_text: str,
    thread_summary: str = "",
    sender_address: str | None = None,
    sender_domain: str | None = None,
    routing_category: str | None = None,
    atomization_text: str | None = None,
) -> None:
    if not getattr(settings, "feedback_atoms_enabled", False):
        return
    try:
        from app.services.feedback_draft_context import paired_retrieval_enabled
    except ImportError:
        return
    if not paired_retrieval_enabled(settings, mailbox):
        return
    _ = (
        session,
        client,
        openai_client,
        email_text,
        thread_summary,
        sender_address,
        sender_domain,
        routing_category,
        atomization_text,
    )
    logger.debug(
        "workflow_extraction_deferred",
        mailbox=mailbox,
        thread_id=str(thread_id),
    )
