"""Scope-decay sweeper — daily job that expires narrow-scope atoms and notes.

Deactivates feedback_atoms and archives teaching_notes whose expires_at is in
the past. Mail.Read only — no email is read here; this only sweeps local DB rows.
"""

from __future__ import annotations

import structlog
from sqlalchemy import String, cast, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.db.session import get_session_factory
from app.models.db.feedback_atom import FeedbackAtom
from app.models.db.teaching_note import TeachingNote
from app.models.db.thread import Thread
from app.models.schemas.email import ThreadStateEnum

logger = structlog.get_logger(__name__)


async def sweep_expired(session: AsyncSession) -> tuple[int, int]:
    """Deactivate expired atoms and archive expired teaching notes.

    Returns (atoms_deactivated, notes_archived). Exported for direct use
    in tests that supply their own session.
    """
    atoms_result = await session.execute(
        update(FeedbackAtom)
        .where(
            FeedbackAtom.is_active.is_(True),
            FeedbackAtom.expires_at.is_not(None),
            FeedbackAtom.expires_at < func.now(),
        )
        .values(is_active=False)
    )
    atoms_deactivated = int(getattr(atoms_result, "rowcount", 0) or 0)

    resolved_thread_keys = select(func.concat("thread:", cast(Thread.id, String))).where(
        Thread.state == ThreadStateEnum.RESOLVED.value,
        Thread.last_updated_at < func.now() - text("interval '30 days'"),
    )
    resolved_atoms = await session.execute(
        update(FeedbackAtom)
        .where(
            FeedbackAtom.is_active.is_(True),
            FeedbackAtom.scope == "thread",
            FeedbackAtom.scope_key.in_(resolved_thread_keys),
        )
        .values(is_active=False)
    )
    atoms_deactivated += int(getattr(resolved_atoms, "rowcount", 0) or 0)

    notes_result = await session.execute(
        update(TeachingNote)
        .where(
            TeachingNote.status == "active",
            TeachingNote.expires_at.is_not(None),
            TeachingNote.expires_at < func.now(),
        )
        .values(status="archived")
    )
    notes_archived = int(getattr(notes_result, "rowcount", 0) or 0)

    return atoms_deactivated, notes_archived


async def run_scope_decay() -> None:
    """Scheduled entry point: open a fresh session and run the sweep."""
    factory = get_session_factory()
    try:
        async with factory() as session, session.begin():
            atoms_deactivated, notes_archived = await sweep_expired(session)
        logger.info(
            "scope_decay_complete",
            atoms_deactivated=atoms_deactivated,
            notes_archived=notes_archived,
        )
    except Exception:
        logger.exception("scope_decay_failed")
