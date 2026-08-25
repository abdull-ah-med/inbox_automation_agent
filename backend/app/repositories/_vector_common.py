"""Shared pgvector session knobs. Every ANN query must set ef_search locally."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings


def cap_limit(limit: int, *, maximum: int, minimum: int = 1) -> int:
    return max(minimum, min(limit, maximum))


async def set_hnsw_session_defaults(session: AsyncSession, settings: Settings) -> None:
    """SET LOCAL hnsw.ef_search (and iterative_scan when enabled) for this txn."""
    ef_search = int(settings.hnsw_ef_search)
    await session.execute(text(f"SET LOCAL hnsw.ef_search = {ef_search}"))
    if settings.hnsw_iterative_scan_enabled:
        await session.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
