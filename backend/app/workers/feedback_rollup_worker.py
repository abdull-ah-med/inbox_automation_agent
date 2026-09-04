"""Feedback rollup worker — weekly precision/coverage metrics for feedback atoms.

Runs a cheap SQL scan per mailbox to aggregate atom precision histograms
and urgency rule override rates, then emits a structured log line.
Always-on (cheap SQL); no LLM calls.
"""

from __future__ import annotations

import structlog
from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.models.db.feedback_atom import FeedbackAtom
from app.models.db.urgency_rule import UrgencyRule

logger = structlog.get_logger(__name__)

_LOW_PRECISION_THRESHOLD = 0.5
_LOW_PRECISION_MIN_N = 20


async def run_weekly_feedback_rollup() -> None:
    """Emit weekly feedback-atom precision summary per mailbox.

    Swallows all errors so it never kills the scheduler.
    """
    get_settings()
    session_factory = get_session_factory()
    try:
        async with session_factory() as session:
            # Distinct mailboxes with active atoms
            mailbox_stmt = (
                select(FeedbackAtom.mailbox).where(FeedbackAtom.is_active.is_(True)).distinct()
            )
            mailboxes_result = await session.execute(mailbox_stmt)
            mailboxes = [row[0] for row in mailboxes_result.all()]

            for mailbox in mailboxes:
                # Total active atoms for this mailbox
                atom_count_stmt = (
                    select(func.count())
                    .select_from(FeedbackAtom)
                    .where(
                        FeedbackAtom.mailbox == mailbox,
                        FeedbackAtom.is_active.is_(True),
                    )
                )
                atom_count = (await session.execute(atom_count_stmt)).scalar_one()

                # Atoms with low precision (precision_den > 0 and ratio < threshold)
                all_atoms_stmt = select(
                    FeedbackAtom.precision_num, FeedbackAtom.precision_den
                ).where(
                    FeedbackAtom.mailbox == mailbox,
                    FeedbackAtom.is_active.is_(True),
                    FeedbackAtom.precision_den > 0,
                )
                atoms_result = await session.execute(all_atoms_stmt)
                low_precision_count = sum(
                    1
                    for num, den in atoms_result.all()
                    if den >= _LOW_PRECISION_MIN_N and (num / den) < _LOW_PRECISION_THRESHOLD
                )

                # Urgency rule override rate for this mailbox
                rules_stmt = select(
                    func.sum(UrgencyRule.override_count),
                    func.sum(UrgencyRule.hit_count),
                ).where(UrgencyRule.mailbox == mailbox)
                rules_result = await session.execute(rules_stmt)
                override_count, hit_count = rules_result.one()
                override_count = override_count or 0
                hit_count = hit_count or 0
                override_rate = (override_count / hit_count) if hit_count > 0 else 0.0

                logger.info(
                    "feedback_weekly_rollup",
                    mailbox=mailbox,
                    atom_count=atom_count,
                    low_precision_count=low_precision_count,
                    urgency_rule_hit_count=hit_count,
                    urgency_rule_override_count=override_count,
                    urgency_rule_override_rate=round(override_rate, 4),
                )
    except Exception:
        logger.exception("feedback_rollup_worker_failed")
