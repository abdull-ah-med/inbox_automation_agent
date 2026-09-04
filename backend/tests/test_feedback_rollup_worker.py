"""Feedback rollup worker tests.

Worked example (hand-counted):
  Mailbox sales@example.com has 3 active atoms:
    A: precision 1/20 = 0.05 → low (< 0.5 with n>=20)
    B: precision 16/20 = 0.80 → not low
    C: precision_den=0 → excluded from low-precision ratio
  Urgency rules for same mailbox: override_count sum=2, hit_count sum=10
    → override_rate = 0.2

Oracle: log event feedback_weekly_rollup with
  atom_count=3, low_precision_count=1,
  urgency_rule_hit_count=10, urgency_rule_override_count=2,
  urgency_rule_override_rate=0.2
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from structlog.testing import capture_logs

from app.models.db.feedback_atom import FeedbackAtom
from app.models.db.urgency_rule import UrgencyRule
from app.workers.feedback_rollup_worker import run_weekly_feedback_rollup

pytestmark = pytest.mark.db

MAILBOX = "sales@example.com"
OTHER = "other@example.com"


def _unit_vec(i: int) -> list[float]:
    v = [0.0] * 1536
    v[i % 1536] = 1.0
    return v


async def _seed_atom(
    session,
    *,
    mailbox: str,
    precision_num: int,
    precision_den: int,
    active: bool = True,
) -> None:
    session.add(
        FeedbackAtom(
            id=uuid.uuid4(),
            source_kind="preference_pair",
            source_id=uuid.uuid4(),
            mailbox=mailbox,
            atom_text=f"atom-{precision_num}-{precision_den}",
            atom_embedding=_unit_vec(precision_num + precision_den),
            role="Fix",
            applies_when=None,
            scope="mailbox",
            scope_key=mailbox,
            is_active=active,
            hit_count=precision_den,
            precision_num=precision_num,
            precision_den=precision_den,
            expires_at=None,
            person_bound=False,
            created_at=datetime.now(UTC),
        )
    )


async def _seed_rule(
    session,
    *,
    mailbox: str,
    hit_count: int,
    override_count: int,
) -> None:
    session.add(
        UrgencyRule(
            id=uuid.uuid4(),
            mailbox=mailbox,
            scope="sender_domain",
            scope_key="domain:example.com",
            condition={"sender_domain": "example.com"},
            action={"set_urgency_floor": "LOW"},
            status="active",
            hit_count=hit_count,
            override_count=override_count,
            person_bound=False,
            created_at=datetime.now(UTC),
        )
    )


@pytest.mark.asyncio
async def test_weekly_rollup_emits_hand_counted_metrics(db_session) -> None:
    await _seed_atom(db_session, mailbox=MAILBOX, precision_num=1, precision_den=20)
    await _seed_atom(db_session, mailbox=MAILBOX, precision_num=16, precision_den=20)
    await _seed_atom(db_session, mailbox=MAILBOX, precision_num=0, precision_den=0)
    # Inactive atom must not count
    await _seed_atom(db_session, mailbox=MAILBOX, precision_num=0, precision_den=5, active=False)
    # Other mailbox must not bleed into sales metrics
    await _seed_atom(db_session, mailbox=OTHER, precision_num=0, precision_den=5)
    await _seed_rule(db_session, mailbox=MAILBOX, hit_count=7, override_count=1)
    await _seed_rule(db_session, mailbox=MAILBOX, hit_count=3, override_count=1)
    await db_session.commit()

    class _Factory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *args):
            return False

    with (
        patch(
            "app.workers.feedback_rollup_worker.get_session_factory",
            return_value=_Factory(),
        ),
        capture_logs() as entries,
    ):
        await run_weekly_feedback_rollup()

    sales_events = [
        e
        for e in entries
        if e.get("event") == "feedback_weekly_rollup" and e.get("mailbox") == MAILBOX
    ]
    assert len(sales_events) == 1
    event = sales_events[0]
    # Hand-counted oracles from the fixture above
    assert event["atom_count"] == 3
    assert event["low_precision_count"] == 1  # only 1/10
    assert event["urgency_rule_hit_count"] == 10  # 7+3
    assert event["urgency_rule_override_count"] == 2  # 1+1
    assert event["urgency_rule_override_rate"] == 0.2
