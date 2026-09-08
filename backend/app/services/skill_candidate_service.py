"""Skill candidate service — propose from reject clusters; accept/dismiss."""

from __future__ import annotations

import uuid

import structlog
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import SkillNameConflictError, SkillNotFoundError
from app.db.session import get_session_factory
from app.llm import skill_candidate_propose
from app.models.schemas.routing import normalize_routing_category
from app.models.schemas.skill import SkillCreateSchema, SkillResponseSchema
from app.models.schemas.skill_candidate import SkillCandidateResponseSchema
from app.repositories import (
    rejection_memory_repo,
    skill_candidate_repo,
    skill_repo,
)
from app.services import skill_embedding_service

logger = structlog.get_logger(__name__)

REJECT_CLUSTER_THRESHOLD = 3


async def maybe_propose_from_rejects(
    *,
    settings: Settings,
    anthropic_client: AsyncAnthropic | None,
    mailbox: str,
    routing_category: str,
    reason_code: str,
) -> None:
    """After reject store: if count≥3 and no open candidate, propose one. Best-effort."""
    if anthropic_client is None or not settings.anthropic_api_key.strip():
        return

    category = normalize_routing_category(routing_category)
    try:
        factory = get_session_factory()
        async with factory() as session:
            count = await rejection_memory_repo.count_for_bucket(
                session,
                mailbox=mailbox,
                routing_category=category,
                reason_code=reason_code,
            )
            if count < REJECT_CLUSTER_THRESHOLD:
                if session.in_transaction():
                    await session.commit()
                return

            if await skill_candidate_repo.has_open_for_bucket(
                session,
                mailbox=mailbox,
                routing_category=category,
                reason_code=reason_code,
            ):
                if session.in_transaction():
                    await session.commit()
                return

            notes = await rejection_memory_repo.list_notes_for_bucket(
                session,
                mailbox=mailbox,
                routing_category=category,
                reason_code=reason_code,
                limit=REJECT_CLUSTER_THRESHOLD,
            )
            source_ids = await rejection_memory_repo.list_ids_for_bucket(
                session,
                mailbox=mailbox,
                routing_category=category,
                reason_code=reason_code,
                limit=REJECT_CLUSTER_THRESHOLD,
            )
            if session.in_transaction():
                await session.commit()

        if len(notes) < REJECT_CLUSTER_THRESHOLD:
            return

        proposed = await skill_candidate_propose.propose_skill_from_notes(
            client=anthropic_client,
            settings=settings,
            mailbox=mailbox,
            routing_category=category,
            reason_code=reason_code,
            notes=notes,
        )

        async with factory() as persist_session:
            # Re-check race: another reject may have inserted already.
            if await skill_candidate_repo.has_open_for_bucket(
                persist_session,
                mailbox=mailbox,
                routing_category=category,
                reason_code=reason_code,
            ):
                if persist_session.in_transaction():
                    await persist_session.commit()
                return
            await skill_candidate_repo.create_candidate(
                persist_session,
                mailbox=mailbox,
                routing_category=category,
                reason_code=reason_code,
                proposed_name=proposed.proposed_name,
                proposed_content=proposed.proposed_content,
                source_rejection_ids=source_ids,
            )
            if persist_session.in_transaction():
                await persist_session.commit()
            logger.info(
                "skill_candidate_proposed",
                mailbox=mailbox,
                routing_category=category,
                reason_code=reason_code,
            )
    except Exception:
        logger.exception(
            "skill_candidate_propose_failed",
            mailbox=mailbox,
            routing_category=category,
            reason_code=reason_code,
        )


async def get_candidate(
    session: AsyncSession,
    candidate_id: uuid.UUID,
) -> SkillCandidateResponseSchema | None:
    """Load one skill candidate row for authorization checks."""
    return await skill_candidate_repo.get_by_id(session, candidate_id)


async def require_allowed_candidate(
    session: AsyncSession,
    settings: Settings,
    candidate_id: uuid.UUID,
) -> SkillCandidateResponseSchema:
    """Return the candidate when its mailbox is in TARGET_MAILBOXES."""
    candidate = await get_candidate(session, candidate_id)
    if candidate is None or not settings.mailbox_allowed(candidate.mailbox):
        raise SkillNotFoundError(f"Skill candidate not found: {candidate_id}")
    return candidate


async def list_pending(
    session: AsyncSession,
    *,
    mailbox: str | None = None,
    mailboxes: list[str] | None = None,
) -> list[SkillCandidateResponseSchema]:
    return await skill_candidate_repo.list_candidates(
        session,
        status="pending",
        mailbox=mailbox,
        mailboxes=mailboxes,
    )


async def accept_candidate(
    session: AsyncSession,
    candidate_id: uuid.UUID,
    *,
    settings: Settings,
    openai_client: AsyncOpenAI | None = None,
) -> SkillResponseSchema:
    """Accept → create Skill + mark accepted + exclude source rejection memories."""
    candidate = await skill_candidate_repo.get_by_id(session, candidate_id)
    if candidate is None:
        raise SkillNotFoundError(f"Skill candidate not found: {candidate_id}")
    if candidate.status != "pending":
        raise SkillNotFoundError(f"Skill candidate not pending: {candidate_id}")

    create = SkillCreateSchema(
        name=candidate.proposed_name,
        description=f"From rejects ({candidate.reason_code})",
        content=candidate.proposed_content,
        category=normalize_routing_category(candidate.routing_category),
        always_apply=False,
        is_active=True,
    )
    try:
        skill = await skill_repo.create(session, create)
    except SkillNameConflictError:
        # Append short suffix to avoid blocking accept on name clash.
        create = SkillCreateSchema(
            name=f"{candidate.proposed_name} ({candidate.reason_code})",
            description=create.description,
            content=create.content,
            category=create.category,
            always_apply=False,
            is_active=True,
        )
        skill = await skill_repo.create(session, create)

    await skill_candidate_repo.set_status(session, candidate_id, status="accepted")
    if candidate.source_rejection_ids:
        await rejection_memory_repo.set_excluded_for_ids(
            session,
            candidate.source_rejection_ids,
            is_excluded=True,
        )

    # Best-effort embed after flush (caller commits).
    await skill_embedding_service.maybe_embed_skill(
        session,
        skill_id=skill.id,
        name=skill.name,
        description=skill.description,
        settings=settings,
        openai_client=openai_client,
    )
    refreshed = await skill_repo.get_by_id(session, skill.id)
    return refreshed or skill


async def dismiss_candidate(
    session: AsyncSession,
    candidate_id: uuid.UUID,
) -> SkillCandidateResponseSchema:
    candidate = await skill_candidate_repo.get_by_id(session, candidate_id)
    if candidate is None:
        raise SkillNotFoundError(f"Skill candidate not found: {candidate_id}")
    if candidate.status != "pending":
        return candidate
    updated = await skill_candidate_repo.set_status(
        session,
        candidate_id,
        status="dismissed",
    )
    if updated is None:
        raise SkillNotFoundError(f"Skill candidate not found: {candidate_id}")
    return updated
