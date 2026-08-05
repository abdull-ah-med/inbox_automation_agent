"""Best-effort skill embedding updates (name + description + body head)."""

from __future__ import annotations

import uuid

import structlog
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.repositories import skill_repo
from app.services import embedding_service

logger = structlog.get_logger(__name__)

_BODY_EMBED_CHARS = 400


def build_embedding_source(
    *,
    name: str,
    description: str | None,
    body: str | None = None,
) -> str:
    """Build the text embedded for skill retrieval ranking.

    Only used as a cosine pre-filter signal — never injected into LLM prompts.
    Uses name + description + the first ~400 chars of body so trigger vocabulary
    at the top of SKILL.md dominates the vector.
    """
    parts = [name.strip()]
    if description and description.strip():
        parts.append(description.strip())
    if body and body.strip():
        parts.append(body.strip()[:_BODY_EMBED_CHARS])
    return "\n\n".join(part for part in parts if part)


async def maybe_embed_skill(
    session: AsyncSession,
    *,
    skill_id: uuid.UUID,
    name: str,
    description: str | None,
    settings: Settings,
    openai_client: AsyncOpenAI | None,
    body: str | None = None,
) -> None:
    """Embed retrieval source and persist. Never raises."""
    if openai_client is None or not settings.openai_api_key.strip():
        return
    try:
        text = build_embedding_source(name=name, description=description, body=body)
        if not text.strip():
            return
        vector = await embedding_service.embed_text(
            text,
            client=openai_client,
            settings=settings,
        )
        await skill_repo.update_embedding(session, skill_id, embedding=vector)
    except Exception:
        logger.exception(
            "skill_embed_failed",
            skill_id=str(skill_id),
        )
