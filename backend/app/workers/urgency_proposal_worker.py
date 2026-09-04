"""Urgency proposal worker — nightly propose_from_edits for each mailbox.

Clusters urgency_feedbacks into promotion proposals when >= 3 edits share the
same sender_domain and direction. Mail.Read only — no email is sent or mutated.
"""

from __future__ import annotations

import structlog

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.services.feedback_atom_service import propose_widenings
from app.services.urgency_rule_service import propose_from_edits

logger = structlog.get_logger(__name__)


async def run_urgency_proposals() -> None:
    """Run propose_from_edits for every configured mailbox."""
    settings = get_settings()
    if not settings.promotion_proposals_enabled:
        logger.info("urgency_proposal_worker_disabled")
        return

    mailboxes = settings.mailbox_list
    if not mailboxes:
        logger.info("urgency_proposal_worker_skipped_no_mailboxes")
        return

    factory = get_session_factory()
    total_proposals = 0

    for mailbox in mailboxes:
        try:
            async with factory() as session:
                proposals = await propose_from_edits(session, mailbox)
                widenings = await propose_widenings(session, mailbox)
                if session.in_transaction():
                    await session.commit()
                total_proposals += len(proposals) + len(widenings)
                logger.info(
                    "urgency_proposals_generated",
                    mailbox=mailbox,
                    count=len(proposals),
                    atom_widenings=len(widenings),
                )
        except Exception:
            logger.exception("urgency_proposal_worker_failed", mailbox=mailbox)

    logger.info("urgency_proposal_worker_complete", total_proposals=total_proposals)
