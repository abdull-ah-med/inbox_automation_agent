"""DB tests for the person_bound promotion wall.

§6.4: can_promote_to_global refuses person_bound=True even when two mailboxes
each have ≥30 hits on matching rules.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.db.urgency_rule import UrgencyRule
from app.repositories.promotion_proposal_repo import PromotionProposalSchema
from app.services.promotion_gate import can_promote_to_global

pytestmark = pytest.mark.db

EXPIRES = datetime.now(UTC) + timedelta(days=30)
DOMAIN = "vendor.com"
SALES = "sales@example.com"
OPS = "ops@example.com"


def _proposal(*, person_bound: bool) -> PromotionProposalSchema:
    payload: dict = {
        "condition": {"sender_domain": DOMAIN},
        "action": {"set_urgency_floor": "LOW"},
    }
    if person_bound:
        payload["person_bound"] = True
    return PromotionProposalSchema(
        id=uuid.uuid4(),
        mailbox=SALES,
        kind="urgency_rule",
        payload=payload,
        impact_num=60,
        impact_den=60,
        evidence_ids=[],
        status="pending",
        expires_at=EXPIRES,
        created_at=datetime.now(UTC),
    )


def _rule(mailbox: str, *, hit_count: int = 30, person_bound: bool = False) -> UrgencyRule:
    return UrgencyRule(
        mailbox=mailbox,
        scope="sender_domain",
        scope_key=f"domain:{DOMAIN}",
        condition={"sender_domain": DOMAIN},
        action={"set_urgency_floor": "LOW"},
        status="active",
        hit_count=hit_count,
        person_bound=person_bound,
    )


async def _seed_two_mailbox_hits(session) -> None:
    session.add(_rule(SALES, hit_count=30))
    session.add(_rule(OPS, hit_count=30))
    await session.flush()


async def test_person_bound_refuses_global_with_two_mailboxes_and_thirty_hits(
    db_session,
) -> None:
    """person_bound=True is a hard veto even with 2x30 hits seeded."""
    await _seed_two_mailbox_hits(db_session)
    result = await can_promote_to_global(db_session, _proposal(person_bound=True))
    assert result is False


async def test_person_bound_false_with_sufficient_evidence_allows(
    db_session,
) -> None:
    """person_bound=False + 2 mailboxes x 30 hits → global is allowed."""
    await _seed_two_mailbox_hits(db_session)
    result = await can_promote_to_global(db_session, _proposal(person_bound=False))
    assert result is True


async def test_person_bound_refuses_global_with_many_mailboxes(
    db_session,
) -> None:
    """person_bound=True is refused even with 5 evidence mailboxes."""
    for i in range(5):
        db_session.add(_rule(f"box{i}@example.com", hit_count=40))
    await db_session.flush()

    result = await can_promote_to_global(db_session, _proposal(person_bound=True))
    assert result is False
