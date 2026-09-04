"""DB tests for promotion_gate.evaluate_against_golden_set.

Worked example — golden-set regression check:

Fixture: One golden case with expected_urgency=HIGH (represents a real incident email
         that must stay HIGH). A proposal attempts to set urgency_floor=LOW for
         any sender_domain email matching the body keywords.

Oracle:
  - Regressing proposal (changes HIGH → LOW on golden case): gate returns False.
  - Non-regressing proposal (sets LOW for unrelated domain with body keyword guard
    that doesn't match any golden case body): gate returns True.
  - No golden cases for mailbox: gate returns False (fail closed).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.db.golden_set_case import GoldenSetCase
from app.repositories.promotion_proposal_repo import PromotionProposalSchema
from app.services.promotion_gate import evaluate_against_golden_set

pytestmark = pytest.mark.db

MAILBOX = "sales@example.com"
EXPIRES = datetime.now(UTC) + timedelta(days=30)


def _make_proposal(
    *,
    mailbox: str = MAILBOX,
    kind: str = "urgency_rule",
    sender_domain: str = "statuspage.io",
    new_urgency: str = "LOW",
    body_contains_any: list[str] | None = None,
) -> PromotionProposalSchema:
    payload: dict = {
        "condition": {"sender_domain": sender_domain},
        "action": {"set_urgency_floor": new_urgency},
    }
    if body_contains_any:
        payload["condition"]["body_contains_any"] = body_contains_any
    return PromotionProposalSchema(
        id=uuid.uuid4(),
        mailbox=mailbox,
        kind=kind,
        payload=payload,
        impact_num=5,
        impact_den=5,
        evidence_ids=[],
        status="pending",
        expires_at=EXPIRES,
        created_at=datetime.now(UTC),
    )


async def test_regressing_proposal_fails_gate(db_session) -> None:
    """Proposal setting LOW conflicts with a golden case that expects HIGH → False."""
    golden = GoldenSetCase(
        id=uuid.uuid4(),
        mailbox=MAILBOX,
        email_text=(
            "Subject: Client escalation — urgent from statuspage.io\n\n"
            "The driver's drug screen came back positive. Please advise immediately."
        ),
        expected_urgency="HIGH",
        expected_action="action_needed",
    )
    db_session.add(golden)
    await db_session.flush()

    # Proposal: change any email to LOW — would regress the HIGH golden case
    proposal = _make_proposal(new_urgency="LOW")
    result = await evaluate_against_golden_set(db_session, proposal)

    # Oracle: HIGH ≠ LOW → gate blocks the proposal
    assert result is False


async def test_non_regressing_proposal_passes_gate(db_session) -> None:
    """Proposal that agrees with the golden case's expected urgency → True."""
    golden = GoldenSetCase(
        id=uuid.uuid4(),
        mailbox=MAILBOX,
        email_text=(
            "Subject: Automated status page update from statuspage.io\n\n"
            "All systems are resolved. No action required."
        ),
        expected_urgency="LOW",
        expected_action="no_action_discarded",
    )
    db_session.add(golden)
    await db_session.flush()

    # Proposal also sets LOW — no regression
    proposal = _make_proposal(new_urgency="LOW")
    result = await evaluate_against_golden_set(db_session, proposal)

    # Oracle: LOW == LOW → no regression → gate passes
    assert result is True


async def test_no_golden_cases_fails_gate(db_session) -> None:
    """Empty golden set for mailbox → gate fails closed."""
    proposal = _make_proposal(mailbox="empty@example.com")
    result = await evaluate_against_golden_set(db_session, proposal)

    assert result is False


async def test_non_urgency_rule_kind_fails_closed_without_llm_judge(db_session) -> None:
    """Atom/note widenings fail closed until an LLM draft-body judge exists."""
    golden = GoldenSetCase(
        id=uuid.uuid4(),
        mailbox=MAILBOX,
        email_text="Subject: test\n\nSome content.",
        expected_urgency="HIGH",
    )
    db_session.add(golden)
    await db_session.flush()

    proposal = _make_proposal(kind="teaching_note")
    result = await evaluate_against_golden_set(db_session, proposal)

    assert result is False


async def test_body_keyword_guard_excludes_non_matching_case(db_session) -> None:
    """Rule with body_contains_any guard doesn't fire on cases without those keywords."""
    golden = GoldenSetCase(
        id=uuid.uuid4(),
        mailbox=MAILBOX,
        email_text=(
            "Subject: Invoice for Q3\n\n"
            "Please process the attached invoice at your earliest convenience."
        ),
        expected_urgency="NORMAL",
    )
    db_session.add(golden)
    await db_session.flush()

    # Guard: only fires when body contains "resolved" or "incident"
    # The golden case body has neither → rule wouldn't fire → no regression
    proposal = _make_proposal(
        new_urgency="LOW",
        body_contains_any=["resolved", "incident"],
    )
    result = await evaluate_against_golden_set(db_session, proposal)

    # Oracle: keyword guard prevents the rule from firing on the golden case → True
    assert result is True


async def test_unrelated_sender_domain_does_not_fail_gate(db_session) -> None:
    """A statuspage.io rule must not fail a golden case that never mentions that domain."""
    golden = GoldenSetCase(
        id=uuid.uuid4(),
        mailbox=MAILBOX,
        email_text=(
            "Subject: Client escalation — urgent\n\n"
            "The driver's drug screen came back positive. Please advise immediately."
        ),
        expected_urgency="HIGH",
        expected_action="action_needed",
    )
    db_session.add(golden)
    await db_session.flush()

    proposal = _make_proposal(sender_domain="statuspage.io", new_urgency="LOW")
    result = await evaluate_against_golden_set(db_session, proposal)
    assert result is True


async def test_sender_domain_column_matches_without_body_mention(db_session) -> None:
    """sender_domain on the case fires the rule even when email_text omits the host."""
    golden = GoldenSetCase(
        id=uuid.uuid4(),
        mailbox=MAILBOX,
        sender_domain="statuspage.io",
        email_text=(
            "Subject: Client escalation — urgent\n\n"
            "The driver's drug screen came back positive. Please advise immediately."
        ),
        expected_urgency="HIGH",
        expected_action="action_needed",
    )
    db_session.add(golden)
    await db_session.flush()

    proposal = _make_proposal(sender_domain="statuspage.io", new_urgency="LOW")
    result = await evaluate_against_golden_set(db_session, proposal)
    assert result is False
