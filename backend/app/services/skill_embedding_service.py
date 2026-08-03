"""Best-effort skill embedding updates (name + description)."""

from __future__ import annotations

import uuid

import structlog
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.repositories import skill_repo
from app.services import embedding_service

logger = structlog.get_logger(__name__)


async def maybe_embed_skill(
    session: AsyncSession,
    *,
    skill_id: uuid.UUID,
    name: str,
    description: str | None,
    settings: Settings,
    openai_client: AsyncOpenAI | None,
) -> None:
    """Embed name+description and persist. Never raises."""
    if openai_client is None or not settings.openai_api_key.strip():
        return
    try:
        text = f"{name}\n{(description or '').strip()}".strip()
        if not text:
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
