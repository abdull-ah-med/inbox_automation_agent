"""DB tests for promotion_proposal_repo.

Worked example:
  - Create a pending proposal with impact_num=4, impact_den=10
  - list_pending returns exactly 1 row
  - set_status to "accepted" -> status changes; list_pending returns 0
  - evidence_ids list is preserved exactly (2 UUIDs)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.repositories import promotion_proposal_repo

pytestmark = pytest.mark.db

MAILBOX = "sales@example.com"
EXPIRES = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)

EVIDENCE_A = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
EVIDENCE_B = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000002")


async def test_create_pending_proposal_impact_num(db_session) -> None:
    """Proposal is stored with impact_num=4; status defaults to pending."""
    proposal = await promotion_proposal_repo.create_promotion_proposal(
        db_session,
        mailbox=MAILBOX,
        kind="urgency_rule",
        payload={"description": "The last 4 alerts from @statuspage.io were LOW."},
        impact_num=4,
        impact_den=10,
        evidence_ids=[EVIDENCE_A, EVIDENCE_B],
        expires_at=EXPIRES,
    )

    assert proposal.status == "pending"
    assert proposal.impact_num == 4  # literal from fixture
    assert proposal.impact_den == 10
    assert proposal.mailbox == MAILBOX
    assert len(proposal.evidence_ids) == 2  # hand-counted: two UUIDs
    assert EVIDENCE_A in proposal.evidence_ids
    assert EVIDENCE_B in proposal.evidence_ids


async def test_list_pending_returns_only_pending(db_session) -> None:
    """list_pending returns 1 row; after accepting it returns 0."""
    proposal = await promotion_proposal_repo.create_promotion_proposal(
        db_session,
        mailbox=MAILBOX,
        kind="atom_widening",
        payload={"target_scope": "mailbox"},
        impact_num=4,
        impact_den=10,
        evidence_ids=[EVIDENCE_A],
        expires_at=EXPIRES,
    )

    pending = await promotion_proposal_repo.list_pending_proposals(db_session, mailbox=MAILBOX)
    assert len(pending) == 1

    await promotion_proposal_repo.set_proposal_status(db_session, proposal.id, "accepted")

    still_pending = await promotion_proposal_repo.list_pending_proposals(
        db_session, mailbox=MAILBOX
    )
    assert len(still_pending) == 0  # accepted; no longer pending


async def test_set_status_returns_updated_row(db_session) -> None:
    """set_proposal_status returns the updated proposal with the new status."""
    proposal = await promotion_proposal_repo.create_promotion_proposal(
        db_session,
        mailbox=MAILBOX,
        kind="note_widening",
        payload={"scope": "sender_domain"},
        impact_num=7,
        impact_den=20,
        evidence_ids=[],
        expires_at=EXPIRES,
    )

    updated = await promotion_proposal_repo.set_proposal_status(
        db_session, proposal.id, "dismissed"
    )
    assert updated is not None
    assert updated.id == proposal.id
    assert updated.status == "dismissed"
