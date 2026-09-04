"""DB tests for canary_sweeper_worker.sweep_canary_rules.

Worked example:
  A. canary, 5 hits, 2 overrides → 2/5 = 0.40 > 0.25, n≥4 → archive
  B. canary, 4 hits, 1 override → 1/4 = 0.25 not > 0.25; canary_until future → stay canary
  C. canary, 3 hits, 2 overrides → n<4 so rate ignored; canary_until past → active
  D. canary, 10 hits, 1 override, canary_until past → 0.10 ≤ 0.25 → active
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.db.urgency_rule import UrgencyRule
from app.workers.canary_sweeper_worker import sweep_canary_rules

pytestmark = pytest.mark.db

MAILBOX = "sales@example.com"
NOW = datetime.now(UTC)
FUTURE = NOW + timedelta(hours=12)
PAST = NOW - timedelta(hours=1)


def _canary(
    *,
    hit_count: int,
    override_count: int,
    canary_until: datetime,
) -> UrgencyRule:
    return UrgencyRule(
        mailbox=MAILBOX,
        scope="sender_domain",
        scope_key="domain:statuspage.io",
        condition={"sender_domain": "statuspage.io"},
        action={"set_urgency_floor": "LOW"},
        status="canary",
        canary_until=canary_until,
        hit_count=hit_count,
        override_count=override_count,
    )


async def test_high_override_rate_archives_canary(db_session) -> None:
    rule = _canary(hit_count=5, override_count=2, canary_until=FUTURE)
    db_session.add(rule)
    await db_session.flush()

    archived, activated = await sweep_canary_rules(db_session)
    await db_session.refresh(rule)

    assert archived == 1
    assert activated == 0
    assert rule.status == "archived"


async def test_override_rate_at_threshold_stays_canary(db_session) -> None:
    rule = _canary(hit_count=4, override_count=1, canary_until=FUTURE)
    db_session.add(rule)
    await db_session.flush()

    archived, activated = await sweep_canary_rules(db_session)
    await db_session.refresh(rule)

    assert archived == 0
    assert activated == 0
    assert rule.status == "canary"


async def test_small_n_past_until_activates(db_session) -> None:
    rule = _canary(hit_count=3, override_count=2, canary_until=PAST)
    db_session.add(rule)
    await db_session.flush()

    archived, activated = await sweep_canary_rules(db_session)
    await db_session.refresh(rule)

    assert archived == 0
    assert activated == 1
    assert rule.status == "active"


async def test_past_until_low_override_activates(db_session) -> None:
    rule = _canary(hit_count=10, override_count=1, canary_until=PAST)
    db_session.add(rule)
    await db_session.flush()

    archived, activated = await sweep_canary_rules(db_session)
    await db_session.refresh(rule)

    assert archived == 0
    assert activated == 1
    assert rule.status == "active"


async def test_active_rules_are_untouched(db_session) -> None:
    rule = UrgencyRule(
        mailbox=MAILBOX,
        scope="sender_domain",
        scope_key="domain:other.com",
        condition={"sender_domain": "other.com"},
        action={"set_urgency_floor": "LOW"},
        status="active",
        hit_count=10,
        override_count=9,
    )
    db_session.add(rule)
    await db_session.flush()

    archived, activated = await sweep_canary_rules(db_session)
    await db_session.refresh(rule)

    assert archived == 0
    assert activated == 0
    assert rule.status == "active"
    leftover = (
        await db_session.execute(select(UrgencyRule).where(UrgencyRule.id == rule.id))
    ).scalar_one()
    assert leftover.status == "active"
