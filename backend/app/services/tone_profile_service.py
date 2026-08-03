"""Tone profile service — threshold rebuild + draft-time assembly."""

from __future__ import annotations

import json

import structlog
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.session import get_session_factory
from app.llm import tone_distill
from app.llm.pii_redact import scrub_text
from app.models.schemas.tone_profile import ToneProfileResponseSchema
from app.repositories import draft_repo, tone_profile_repo
from app.services import reply_memory_service

logger = structlog.get_logger(__name__)

APPROVAL_REBUILD_THRESHOLD = 10
DISTILL_SAMPLE_LIMIT = 20
RECENT_EXAMPLE_LIMIT = 3


def _profile_block(profile: ToneProfileResponseSchema) -> str:
    return json.dumps(profile.profile.model_dump(), separators=(",", ":"), ensure_ascii=False)


async def load_for_draft(
    session: AsyncSession,
    *,
    openai_client: AsyncOpenAI | None,
    settings: Settings,
    mailbox: str,
    routing_category: str,
    email_text: str,
) -> tuple[str | None, list[str]]:
    """Return (tone_profile_block, tone_examples) per locked assembly rules.

    If a profile exists for (mailbox, category) or (mailbox, general):
      profile JSON + last 3 approved reply texts.
    Else cold-start: no profile + semantic reply memory.
    """
    category = routing_category or "general"
    try:
        profile = await tone_profile_repo.get_profile(
            session,
            mailbox=mailbox,
            routing_category=category,
        )
        if profile is None and category != "general":
            profile = await tone_profile_repo.get_profile(
                session,
                mailbox=mailbox,
                routing_category="general",
            )

        if profile is not None:
            examples = await draft_repo.list_recent_approved_bodies(
                session,
                mailbox=mailbox,
                routing_category=category,
                limit=RECENT_EXAMPLE_LIMIT,
            )
            if len(examples) < RECENT_EXAMPLE_LIMIT:
                # Prefer category; fill from mailbox-global if thin.
                more = await draft_repo.list_recent_approved_bodies(
                    session,
                    mailbox=mailbox,
                    routing_category=None,
                    limit=RECENT_EXAMPLE_LIMIT,
                )
                seen = set(examples)
                for body in more:
                    if body not in seen:
                        examples.append(body)
                        seen.add(body)
                    if len(examples) >= RECENT_EXAMPLE_LIMIT:
                        break
            return _profile_block(profile), examples[:RECENT_EXAMPLE_LIMIT]

        semantic = await reply_memory_service.find_similar_replies(
            session,
            openai_client=openai_client,
            settings=settings,
            email_text=email_text,
            mailbox=mailbox,
            limit=RECENT_EXAMPLE_LIMIT,
        )
        return None, semantic
    except Exception:
        logger.exception(
            "tone_profile_load_failed",
            mailbox=mailbox,
            routing_category=category,
        )
        return None, []


async def maybe_rebuild(
    *,
    settings: Settings,
    anthropic_client: AsyncAnthropic | None,
    mailbox: str,
    routing_category: str | None = None,
) -> None:
    """Best-effort rebuild when approval threshold is met. Never raises."""
    if anthropic_client is None or not settings.anthropic_api_key.strip():
        return

    category = routing_category or "general"
    try:
        factory = get_session_factory()
        async with factory() as session:
            profile = await tone_profile_repo.get_profile(
                session,
                mailbox=mailbox,
                routing_category=category,
            )
            since = profile.built_at if profile is not None else None
            new_count = await draft_repo.count_approvals(
                session,
                mailbox=mailbox,
                routing_category=category,
                since=since,
            )
            # Prefer in-category samples when enough; else mailbox-global.
            in_cat = await draft_repo.count_approvals(
                session,
                mailbox=mailbox,
                routing_category=category,
                since=None,
            )
            use_category = category if in_cat >= APPROVAL_REBUILD_THRESHOLD else None

            total_approvals = await draft_repo.count_approvals(
                session,
                mailbox=mailbox,
                routing_category=use_category,
                since=None,
            )
            if session.in_transaction():
                await session.commit()

            should_rebuild = new_count >= APPROVAL_REBUILD_THRESHOLD or (
                profile is None and total_approvals >= APPROVAL_REBUILD_THRESHOLD
            )
            if not should_rebuild:
                return

            async with factory() as write_session:
                bodies = await draft_repo.list_recent_approved_bodies(
                    write_session,
                    mailbox=mailbox,
                    routing_category=use_category,
                    limit=DISTILL_SAMPLE_LIMIT,
                )
                if write_session.in_transaction():
                    await write_session.commit()

            if len(bodies) < APPROVAL_REBUILD_THRESHOLD:
                return

            scrubbed = [scrub_text(b) for b in bodies if scrub_text(b).strip()]
            if len(scrubbed) < APPROVAL_REBUILD_THRESHOLD:
                return

            distilled = await tone_distill.distill_tone_profile(
                client=anthropic_client,
                settings=settings,
                reply_bodies=scrubbed,
            )

            async with factory() as persist_session:
                await tone_profile_repo.upsert_profile(
                    persist_session,
                    mailbox=mailbox,
                    routing_category=category,
                    profile=distilled,
                    sample_count=len(scrubbed),
                )
                if persist_session.in_transaction():
                    await persist_session.commit()
                logger.info(
                    "tone_profile_rebuilt",
                    mailbox=mailbox,
                    routing_category=category,
                    sample_count=len(scrubbed),
                )
    except Exception:
        logger.exception(
            "tone_profile_rebuild_failed",
            mailbox=mailbox,
            routing_category=category,
        )


async def list_profiles(
    session: AsyncSession,
    *,
    mailbox: str | None = None,
) -> list[ToneProfileResponseSchema]:
    return await tone_profile_repo.list_profiles(session, mailbox=mailbox)
