"""Persisted draft-regeneration job status on threads.

Survives page reload: Elise confirms rewrite → status=running is committed
before Sonnet runs. Thread detail exposes the flag so the UI can keep showing
"Regenerating draft…" until finish/fail.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import thread_repo

STATUS_IDLE = "idle"
STATUS_RUNNING = "running"
STATUS_FAILED = "failed"


async def begin_regen(
    session: AsyncSession,
    thread_id: uuid.UUID,
    *,
    instruction: str,
) -> bool:
    """Mark the thread as regenerating. ``instruction`` is accepted for callers
    that already validated it; status persistence is the contract here.
    """
    _ = instruction
    updated = await thread_repo.set_draft_regen_status(
        session,
        thread_id,
        status=STATUS_RUNNING,
        error=None,
        started_at=datetime.now(UTC),
    )
    return updated is not None


async def finish_regen(session: AsyncSession, thread_id: uuid.UUID) -> None:
    await thread_repo.set_draft_regen_status(
        session,
        thread_id,
        status=STATUS_IDLE,
        error=None,
        started_at=None,
    )


async def fail_regen(
    session: AsyncSession,
    thread_id: uuid.UUID,
    *,
    error: str,
) -> None:
    await thread_repo.set_draft_regen_status(
        session,
        thread_id,
        status=STATUS_FAILED,
        error=error[:2000],
        started_at=None,
    )
