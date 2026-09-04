"""DB tests for promotion_gate.can_promote_to_global — cross-mailbox barrier.

Worked examples — §6.4: evidence is persisted atoms/rules with hit_count ≥ 30
in ≥ 2 mailboxes (person_bound=False). mailbox_list length is not the oracle.
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


def _proposal(*, person_bound: bool = False) -> PromotionProposalSchema:
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


def _rule(mailbox: str, *, hit_count: int, person_bound: bool = False) -> UrgencyRule:
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


async def test_single_mailbox_with_thirty_hits_refuses_global(db_session) -> None:
    """One mailbox with 30 hits is not enough — need ≥2 evidence mailboxes."""
    db_session.add(_rule(SALES, hit_count=30))
    await db_session.flush()

    result = await can_promote_to_global(db_session, _proposal())
    assert result is False


async def test_two_mailboxes_twenty_hits_each_refuses(db_session) -> None:
    """Two mailboxes but 20 hits each (< 30) → False."""
    db_session.add(_rule(SALES, hit_count=20))
    db_session.add(_rule(OPS, hit_count=20))
    await db_session.flush()

    result = await can_promote_to_global(db_session, _proposal())
    assert result is False


async def test_two_mailboxes_thirty_hits_each_allows(db_session) -> None:
    """Two mailboxes with exactly 30 hits each → True."""
    db_session.add(_rule(SALES, hit_count=30))
    db_session.add(_rule(OPS, hit_count=30))
    await db_session.flush()

    result = await can_promote_to_global(db_session, _proposal())
    assert result is True


async def test_two_mailboxes_one_below_threshold_refuses(db_session) -> None:
    """29 + 30 is still only one qualifying mailbox → False."""
    db_session.add(_rule(SALES, hit_count=29))
    db_session.add(_rule(OPS, hit_count=30))
    await db_session.flush()

    result = await can_promote_to_global(db_session, _proposal())
    assert result is False


async def test_three_mailboxes_thirty_hits_each_allows(db_session) -> None:
    """Three evidence mailboxes at the 30-hit floor → True."""
    db_session.add(_rule(SALES, hit_count=30))
    db_session.add(_rule(OPS, hit_count=30))
    db_session.add(_rule("cr@example.com", hit_count=30))
    await db_session.flush()

    result = await can_promote_to_global(db_session, _proposal())
    assert result is True
