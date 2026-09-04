"""Nightly canary sweeper for urgency rules.

If a canary rule's override rate exceeds 0.25 with n≥4 hits, archive it.
Otherwise, when canary_until has passed, promote to active.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session_factory
from app.models.db.urgency_rule import UrgencyRule
from app.repositories import urgency_rule_repo
from app.services import audit_service

logger = structlog.get_logger(__name__)

_OVERRIDE_RATE_MAX = 0.25
_MIN_HITS = 4


def canary_override_too_high(hits: int, overrides: int) -> bool:
    """True when a canary's override rate exceeds the archive threshold."""
    return hits >= _MIN_HITS and (overrides / hits) > _OVERRIDE_RATE_MAX


async def sweep_canary_rules(session: AsyncSession) -> tuple[int, int]:
    """Archive high-override canaries; activate those past canary_until.

    Returns (archived, activated).
    """
    now = datetime.now(UTC)
    rows = (
        (await session.execute(select(UrgencyRule).where(UrgencyRule.status == "canary")))
        .scalars()
        .all()
    )
    archived = 0
    activated = 0
    for rule in rows:
        hits = rule.hit_count or 0
        overrides = rule.override_count or 0
        if canary_override_too_high(hits, overrides):
            await urgency_rule_repo.set_urgency_rule_status(session, rule.id, "archived")
            archived += 1
            try:
                await audit_service.log_event(
                    session,
                    event_type="urgency_rule.canary_archived",
                    conversation_id=f"urgency_rule:{rule.id}",
                    mailbox=rule.mailbox,
                    payload={
                        "rule_id": str(rule.id),
                        "hit_count": hits,
                        "override_count": overrides,
                    },
                    actor="canary_sweeper",
                )
            except Exception:
                logger.warning("canary_sweeper_audit_failed", rule_id=str(rule.id))
            continue
        until = rule.canary_until
        if until is not None and until.tzinfo is None:
            until = until.replace(tzinfo=UTC)
        if until is not None and until <= now:
            await urgency_rule_repo.set_urgency_rule_status(session, rule.id, "active")
            activated += 1
    return archived, activated


async def run_canary_sweeper() -> None:
    factory = get_session_factory()
    try:
        async with factory() as session, session.begin():
            archived, activated = await sweep_canary_rules(session)
        logger.info(
            "canary_sweeper_complete",
            archived=archived,
            activated=activated,
        )
    except Exception:
        logger.exception("canary_sweeper_failed")
