"""Outlook tip already sent: promote learning and detect existing briefings."""

from __future__ import annotations

import uuid

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models.schemas.email_triage_state import EmailTriageState
from app.repositories import draft_repo, sent_reply_repo, thread_repo
from app.services import sent_reply_learning_service


async def latest_proposed_has_teaching_note(
    session: AsyncSession,
    thread_id: uuid.UUID,
) -> bool:
    draft = await draft_repo.get_latest_proposed_by_thread(session, thread_id)
    return draft is not None and bool((draft.teaching_note or "").strip())


async def promote_and_resolve_sent_tip(
    *,
    state: EmailTriageState,
    thread_id: uuid.UUID,
    openai_client: AsyncOpenAI | None,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    anthropic_client: AsyncAnthropic | None = None,
) -> None:
    """Mark RESOLVED and learn from the Outlook send. Does not change draft_status."""
    from app.models.schemas.email import ThreadStateEnum

    routing = state.triage.routing_category if state.triage is not None else None
    approved = None
    async with session_factory() as session, session.begin():
        sent = await sent_reply_repo.get_by_thread(session, thread_id)
        if sent is not None:
            approved = await sent_reply_learning_service.promote_sent_reply_as_approved(
                session,
                sent_reply=sent,
                settings=settings,
                openai_client=openai_client,
                routing_category=routing,
                anthropic_client=anthropic_client,
            )
        await thread_repo.set_thread_outcome(
            session,
            thread_id,
            state=ThreadStateEnum.RESOLVED.value,
        )

    if approved is not None:
        await sent_reply_learning_service.store_promoted_reply_memory(
            draft=approved,
            settings=settings,
            openai_client=openai_client,
            anthropic_client=anthropic_client,
        )
