"""DB tests for urgency_rule_service.propose_from_edits.

Worked example — cluster ≥3 same-sender-domain edits:

Fixture: 4 urgency_feedback rows for statuspage.io (via urgency_predictions),
         all editing HIGH → LOW for mailbox sales@example.com.
Expected: propose_from_edits creates exactly 1 PromotionProposal with
          impact_num=4 (count of matching feedback rows).

Oracle: 4 edits, threshold is 3, so exactly one cluster qualifies.
        impact_num is the raw cluster count — hand-counted from fixture.
"""

from __future__ import annotations

import uuid

import pytest

from app.models.db.draft import Draft
from app.models.db.thread import Thread
from app.models.db.urgency_feedback import UrgencyFeedback
from app.models.db.urgency_prediction import UrgencyPrediction
from app.services.urgency_rule_service import propose_from_edits

pytestmark = pytest.mark.db

MAILBOX = "sales@example.com"
STATUSPAGE_DOMAIN = "statuspage.io"
ZERO_EMBEDDING = [0.0] * 1536  # placeholder vector for pgvector column


async def _make_thread(session) -> Thread:
    thread = Thread(
        id=uuid.uuid4(),
        mailbox=MAILBOX,
        conversation_id=str(uuid.uuid4()),
        subject="test thread",
        state="new",
    )
    session.add(thread)
    await session.flush()
    return thread


async def _make_draft(session, thread: Thread) -> Draft:
    draft = Draft(
        id=uuid.uuid4(),
        thread_id=thread.id,
        message_id=str(uuid.uuid4()),
        subject="re: test",
        body="draft body",
        recipients={},
        teaching_note="",
    )
    session.add(draft)
    await session.flush()
    return draft


async def _add_feedback_with_prediction(
    session,
    thread: Thread,
    draft: Draft,
    *,
    previous_urgency: str,
    new_urgency: str,
    sender_domain: str,
) -> None:
    """Insert a UrgencyFeedback row paired with a UrgencyPrediction via draft_id."""
    prediction = UrgencyPrediction(
        id=uuid.uuid4(),
        draft_id=draft.id,
        thread_id=thread.id,
        mailbox=MAILBOX,
        sender_domain=sender_domain,
        predicted_urgency=previous_urgency,
        probs={"CRITICAL": 0.0, "HIGH": 1.0, "NORMAL": 0.0, "LOW": 0.0},
        final_urgency=previous_urgency,
    )
    session.add(prediction)

    feedback = UrgencyFeedback(
        id=uuid.uuid4(),
        draft_id=draft.id,
        thread_id=thread.id,
        mailbox=MAILBOX,
        previous_urgency=previous_urgency,
        new_urgency=new_urgency,
        reason="Automated statuspage.io alert; should be LOW.",
        embedding=ZERO_EMBEDDING,
    )
    session.add(feedback)
    await session.flush()


async def test_four_edits_produces_one_proposal_with_correct_impact(db_session) -> None:
    """4 feedback edits for statuspage.io HIGH→LOW → 1 proposal with impact_num=4."""
    # Insert 4 feedback+prediction pairs for the same domain and direction
    for _ in range(4):
        thread = await _make_thread(db_session)
        draft = await _make_draft(db_session, thread)
        await _add_feedback_with_prediction(
            db_session,
            thread,
            draft,
            previous_urgency="HIGH",
            new_urgency="LOW",
            sender_domain=STATUSPAGE_DOMAIN,
        )

    proposals = await propose_from_edits(db_session, MAILBOX)

    # Oracle: 4 edits > threshold 3 → exactly 1 proposal; impact_num == 4
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.impact_num == 4
    assert proposal.kind == "urgency_rule"
    assert proposal.mailbox == MAILBOX
    assert proposal.payload["condition"]["sender_domain"] == STATUSPAGE_DOMAIN
    assert proposal.payload["action"]["set_urgency"] == "LOW"


async def test_two_edits_below_threshold_no_proposal(db_session) -> None:
    """Only 2 edits for a domain — below threshold 3 → no proposals created."""
    for _ in range(2):
        thread = await _make_thread(db_session)
        draft = await _make_draft(db_session, thread)
        await _add_feedback_with_prediction(
            db_session,
            thread,
            draft,
            previous_urgency="HIGH",
            new_urgency="LOW",
            sender_domain=STATUSPAGE_DOMAIN,
        )

    proposals = await propose_from_edits(db_session, MAILBOX)

    # Oracle: 2 < 3 → no proposals
    assert proposals == []


async def test_three_edits_at_threshold_produces_proposal(db_session) -> None:
    """Exactly 3 edits meets the ≥3 threshold → 1 proposal with impact_num=3."""
    for _ in range(3):
        thread = await _make_thread(db_session)
        draft = await _make_draft(db_session, thread)
        await _add_feedback_with_prediction(
            db_session,
            thread,
            draft,
            previous_urgency="NORMAL",
            new_urgency="HIGH",
            sender_domain="vendor.com",
        )

    proposals = await propose_from_edits(db_session, MAILBOX)

    # Oracle: 3 == threshold → 1 proposal; impact_num = 3
    assert len(proposals) == 1
    assert proposals[0].impact_num == 3


async def test_different_domains_produce_separate_proposals(db_session) -> None:
    """3 edits for domain A and 4 for domain B → 2 distinct proposals."""
    for _ in range(3):
        thread = await _make_thread(db_session)
        draft = await _make_draft(db_session, thread)
        await _add_feedback_with_prediction(
            db_session,
            thread,
            draft,
            previous_urgency="HIGH",
            new_urgency="LOW",
            sender_domain="alpha.com",
        )
    for _ in range(4):
        thread = await _make_thread(db_session)
        draft = await _make_draft(db_session, thread)
        await _add_feedback_with_prediction(
            db_session,
            thread,
            draft,
            previous_urgency="HIGH",
            new_urgency="LOW",
            sender_domain="beta.com",
        )

    proposals = await propose_from_edits(db_session, MAILBOX)

    # Oracle: 2 qualifying clusters → 2 proposals
    assert len(proposals) == 2
    domains = {p.payload["condition"]["sender_domain"] for p in proposals}
    assert domains == {"alpha.com", "beta.com"}
    counts = {p.payload["condition"]["sender_domain"]: p.impact_num for p in proposals}
    assert counts["alpha.com"] == 3
    assert counts["beta.com"] == 4


async def test_edits_without_prediction_use_alert_sender_norm(db_session) -> None:
    """4 HIGH→LOW edits with no prediction row still cluster via thread sender."""
    for _ in range(4):
        thread = Thread(
            id=uuid.uuid4(),
            mailbox=MAILBOX,
            conversation_id=str(uuid.uuid4()),
            subject="statuspage resolved",
            state="new",
            alert_sender_norm="alerts@statuspage.io",
        )
        db_session.add(thread)
        await db_session.flush()
        draft = await _make_draft(db_session, thread)
        db_session.add(
            UrgencyFeedback(
                id=uuid.uuid4(),
                draft_id=draft.id,
                thread_id=thread.id,
                mailbox=MAILBOX,
                previous_urgency="HIGH",
                new_urgency="LOW",
                reason="Resolved incident; should be LOW.",
                embedding=ZERO_EMBEDDING,
            )
        )
        await db_session.flush()

    proposals = await propose_from_edits(db_session, MAILBOX)

    assert len(proposals) == 1
    assert proposals[0].impact_num == 4
    assert proposals[0].payload["condition"]["sender_domain"] == STATUSPAGE_DOMAIN
    assert proposals[0].payload["person_bound"] is False


async def test_second_nightly_run_does_not_duplicate_pending(db_session) -> None:
    """Same 4 edits proposed twice → exactly one pending row, impact_num=4."""
    for _ in range(4):
        thread = await _make_thread(db_session)
        draft = await _make_draft(db_session, thread)
        await _add_feedback_with_prediction(
            db_session,
            thread,
            draft,
            previous_urgency="HIGH",
            new_urgency="LOW",
            sender_domain=STATUSPAGE_DOMAIN,
        )

    first = await propose_from_edits(db_session, MAILBOX)
    second = await propose_from_edits(db_session, MAILBOX)

    from sqlalchemy import select

    from app.models.db.promotion_proposal import PromotionProposal

    rows = (
        (
            await db_session.execute(
                select(PromotionProposal).where(
                    PromotionProposal.mailbox == MAILBOX,
                    PromotionProposal.kind == "urgency_rule",
                    PromotionProposal.status == "pending",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(first) == 1
    assert first[0].impact_num == 4
    assert len(rows) == 1
    assert rows[0].impact_num == 4
    assert second[0].id == first[0].id
