"""Skill selection for draft prompts — always_apply + Haiku confirm over pool."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import ClassificationError
from app.llm import skill_selector
from app.llm.email_clean import effective_body_text
from app.models.schemas.classification import TriageResultSchema
from app.models.schemas.draft import AppliedSkillSchema
from app.models.schemas.email import EmailMessageSchema
from app.models.schemas.skill import SkillResponseSchema
from app.repositories import skill_repo
from app.repositories.skill_repo import SkillSelectionRow
from app.services import audit_service, embedding_service

logger = structlog.get_logger(__name__)

_POOL_EMBED_THRESHOLD = 8
_PLACEHOLDER_TS = datetime(2020, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class SelectedSkills:
    """Skill blocks for the draft prompt plus IDs for reference tool use."""

    blocks: list[str] = field(default_factory=list)
    skill_ids: list[uuid.UUID] = field(default_factory=list)
    applied: list[AppliedSkillSchema] = field(default_factory=list)


def format_skill_block(skill: SkillSelectionRow) -> str:
    header = f"## Skill: {skill.name} (id: {skill.id})\n{skill.content.strip()}"
    if not skill.reference_manifest:
        return header
    lines = "\n".join(f"- {path}" for path in skill.reference_manifest)
    return (
        f"{header}\n\n"
        f"### Available reference files\n"
        f"{lines}\n"
        f"(Use the read_skill_reference tool to load any of these.)"
    )


def _to_selector_schema(row: SkillSelectionRow) -> SkillResponseSchema:
    ref_note = ""
    if row.reference_manifest:
        ref_note = f" [refs: {', '.join(row.reference_manifest[:5])}]"
    description = (row.description or "") + ref_note
    return SkillResponseSchema(
        id=row.id,
        name=row.name,
        description=description or None,
        content=row.content,
        category=row.category,
        always_apply=row.always_apply,
        is_active=row.is_active,
        created_at=_PLACEHOLDER_TS,
        updated_at=_PLACEHOLDER_TS,
    )


async def _narrow_pool_by_embedding(
    session: AsyncSession,
    *,
    openai_client: AsyncOpenAI | None,
    settings: Settings,
    email_text: str,
    pool: list[SkillSelectionRow],
) -> list[SkillSelectionRow]:
    """Top-8 by cosine among skills with embeddings; fill with name-sorted nulls."""
    if len(pool) <= _POOL_EMBED_THRESHOLD:
        return pool

    with_embed = [s for s in pool if s.embedding is not None]
    without = sorted(
        [s for s in pool if s.embedding is None],
        key=lambda s: s.name.lower(),
    )

    if openai_client is None or not settings.openai_api_key.strip() or not with_embed:
        return sorted(pool, key=lambda s: s.name.lower())[:_POOL_EMBED_THRESHOLD]

    try:
        vector = await embedding_service.embed_text(
            email_text,
            client=openai_client,
            settings=settings,
        )
        ranked = await skill_repo.rank_by_cosine(
            session,
            query_embedding=vector,
            skill_ids=[s.id for s in with_embed],
            limit=_POOL_EMBED_THRESHOLD,
        )
        ranked_ids = {s.id for s in ranked}
        candidates = list(ranked)
        for skill in without:
            if len(candidates) >= _POOL_EMBED_THRESHOLD:
                break
            if skill.id not in ranked_ids:
                candidates.append(skill)
        return candidates[:_POOL_EMBED_THRESHOLD]
    except Exception:
        logger.exception("skill_pool_embed_narrow_failed")
        return sorted(pool, key=lambda s: s.name.lower())[:_POOL_EMBED_THRESHOLD]


async def select_skills(
    session: AsyncSession,
    *,
    client: AsyncAnthropic,
    settings: Settings,
    openai_client: AsyncOpenAI | None,
    email: EmailMessageSchema,
    triage: TriageResultSchema,
    conversation_id: str | None = None,
) -> SelectedSkills:
    """Return skill blocks + IDs for the draft user turn and tool loop.

    Locked algorithm:
    always_apply + Haiku selection over category/general pool (embed narrow if >8).
    On Haiku failure → always_apply only.
    """
    try:
        active = await skill_repo.list_active_for_selection(session)
    except Exception:
        logger.exception("skill_load_failed")
        return SelectedSkills()

    always = [s for s in active if s.always_apply]
    category = triage.routing_category or "general"
    pool = [
        s
        for s in active
        if not s.always_apply and (s.category or "general") in {category, "general"}
    ]

    always_ids = [s.id for s in always]
    candidate_ids: list[uuid.UUID] = []
    selected_ids: list[uuid.UUID] = []

    body = effective_body_text(
        body_clean=email.body_clean,
        body_text=email.body_text,
        body_content_type=email.body_content_type,
    )
    email_text = f"{email.subject}\n\n{body}"

    selected_from_pool: list[SkillSelectionRow] = []
    if pool:
        candidates = await _narrow_pool_by_embedding(
            session,
            openai_client=openai_client,
            settings=settings,
            email_text=email_text,
            pool=pool,
        )
        candidate_ids = [s.id for s in candidates]
        try:
            chosen = await skill_selector.select_skills(
                client=client,
                settings=settings,
                subject=email.subject,
                body_preview=body,
                triage=triage,
                candidates=[_to_selector_schema(s) for s in candidates],
            )
            by_id = {s.id: s for s in candidates}
            selected_from_pool = [by_id[i] for i in chosen if i in by_id]
            selected_ids = [s.id for s in selected_from_pool]
        except ClassificationError:
            logger.warning(
                "skill_selection_degraded_to_always",
                mailbox=email.mailbox,
                always_count=len(always),
            )
            selected_from_pool = []
            selected_ids = []

    merged: list[SkillSelectionRow] = []
    seen: set[uuid.UUID] = set()
    for skill in always + selected_from_pool:
        if skill.id in seen:
            continue
        seen.add(skill.id)
        merged.append(skill)

    try:
        await audit_service.log_event(
            session,
            event_type="skills.selected",
            conversation_id=conversation_id or email.conversation_id,
            mailbox=email.mailbox,
            payload={
                "candidate_ids": [str(i) for i in candidate_ids],
                "selected_ids": [str(i) for i in selected_ids],
                "always_ids": [str(i) for i in always_ids],
            },
            actor="system",
        )
    except Exception:
        logger.warning(
            "skills_selected_audit_failed",
            mailbox=email.mailbox,
            message_id=email.message_id,
        )

    return SelectedSkills(
        blocks=[format_skill_block(s) for s in merged],
        skill_ids=[s.id for s in merged],
        applied=[AppliedSkillSchema(id=s.id, name=s.name) for s in merged],
    )


async def select_skill_contents(
    session: AsyncSession,
    *,
    client: AsyncAnthropic,
    settings: Settings,
    openai_client: AsyncOpenAI | None,
    email: EmailMessageSchema,
    triage: TriageResultSchema,
    conversation_id: str | None = None,
) -> list[str]:
    """Backward-compatible wrapper returning only formatted skill blocks."""
    selected = await select_skills(
        session,
        client=client,
        settings=settings,
        openai_client=openai_client,
        email=email,
        triage=triage,
        conversation_id=conversation_id,
    )
    return selected.blocks
