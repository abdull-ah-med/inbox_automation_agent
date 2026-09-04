"""DB tests for promotion_service.accept_proposal / revert_promotion.

Worked example — accept then revert lifecycle:

1. Create a pending proposal.
2. accept_proposal → status changes to 'accepted'; an urgency_rule row is created.
3. revert_promotion → status changes to 'archived'; urgency_rule status → 'archived'.

Oracle: status strings are spec-defined literals, not derived from the implementation.
        Checking all three states ensures the lifecycle is correct.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.db.promotion_proposal import PromotionProposal
from app.models.db.urgency_rule import UrgencyRule
from app.services.promotion_service import accept_proposal, revert_promotion

pytestmark = pytest.mark.db

MAILBOX = "sales@example.com"
EXPIRES = datetime.now(UTC) + timedelta(days=30)


async def _create_pending_proposal(session) -> uuid.UUID:
    proposal = PromotionProposal(
        id=uuid.uuid4(),
        mailbox=MAILBOX,
        kind="urgency_rule",
        payload={
            "condition": {"sender_domain": "statuspage.io"},
            "action": {"set_urgency_floor": "LOW"},
            "direction": "down",
            "from_urgency": "HIGH",
        },
        impact_num=5,
        impact_den=5,
        evidence_ids=[],
        status="pending",
        expires_at=EXPIRES,
    )
    session.add(proposal)
    await session.flush()
    return proposal.id


async def test_accept_changes_status_to_accepted(db_session) -> None:
    """accept_proposal transitions a pending proposal to 'accepted' status."""
    proposal_id = await _create_pending_proposal(db_session)

    accepted = await accept_proposal(db_session, proposal_id, skip_gate=True)

    # Oracle: accepted status is the literal "accepted"
    assert accepted.status == "accepted"
    assert accepted.id == proposal_id


async def test_accept_creates_urgency_rule_in_canary(db_session) -> None:
    """Accepting an urgency_rule proposal creates a canary rule in urgency_rules."""
    proposal_id = await _create_pending_proposal(db_session)

    await accept_proposal(db_session, proposal_id, skip_gate=True)

    rules = (
        (
            await db_session.execute(
                select(UrgencyRule).where(
                    UrgencyRule.mailbox == MAILBOX,
                    UrgencyRule.scope_key == "domain:statuspage.io",
                )
            )
        )
        .scalars()
        .all()
    )

    # Oracle: exactly 1 new canary rule created for the domain
    assert len(rules) == 1
    assert rules[0].status == "canary"
    assert rules[0].scope == "sender_domain"


async def test_revert_changes_status_to_reverted(db_session) -> None:
    """revert_promotion transitions an accepted proposal to 'reverted' status."""
    proposal_id = await _create_pending_proposal(db_session)
    await accept_proposal(db_session, proposal_id, skip_gate=True)

    reverted = await revert_promotion(db_session, proposal_id)

    assert reverted.status == "reverted"
    assert reverted.id == proposal_id


async def test_revert_archives_urgency_rule(db_session) -> None:
    """Reverting an urgency_rule proposal archives the corresponding rule."""
    proposal_id = await _create_pending_proposal(db_session)
    await accept_proposal(db_session, proposal_id, skip_gate=True)

    await revert_promotion(db_session, proposal_id)

    rules = (
        (
            await db_session.execute(
                select(UrgencyRule).where(
                    UrgencyRule.mailbox == MAILBOX,
                    UrgencyRule.scope_key == "domain:statuspage.io",
                )
            )
        )
        .scalars()
        .all()
    )

    # Oracle: the canary rule is now archived
    assert len(rules) == 1
    assert rules[0].status == "archived"


async def test_accept_writes_created_rule_id(db_session) -> None:
    proposal_id = await _create_pending_proposal(db_session)
    accepted = await accept_proposal(db_session, proposal_id, skip_gate=True)
    created = accepted.payload.get("created_rule_id")
    assert created is not None
    uuid.UUID(str(created))


async def test_accept_atom_widening_inserts_new_atom(db_session) -> None:
    from app.models.db.feedback_atom import FeedbackAtom

    source = FeedbackAtom(
        id=uuid.uuid4(),
        source_kind="preference_pair",
        source_id=uuid.uuid4(),
        mailbox=MAILBOX,
        atom_text="Always greet by first name",
        atom_embedding=[0.0] * 1535 + [1.0],
        role="Fix",
        scope="sender_domain",
        scope_key="domain:acme.com",
        is_active=True,
    )
    db_session.add(source)
    await db_session.flush()
    proposal = PromotionProposal(
        id=uuid.uuid4(),
        mailbox=MAILBOX,
        kind="atom_widening",
        payload={"from_scope": "sender_domain", "requested_scope": "mailbox"},
        impact_num=3,
        impact_den=3,
        evidence_ids=[source.id],
        status="pending",
        expires_at=EXPIRES,
    )
    db_session.add(proposal)
    await db_session.flush()

    accepted = await accept_proposal(db_session, proposal.id, skip_gate=True)
    created_id = uuid.UUID(accepted.payload["created_atom_id"])
    created = (
        await db_session.execute(select(FeedbackAtom).where(FeedbackAtom.id == created_id))
    ).scalar_one()
    assert created.scope == "mailbox"
    assert created.promoted_from_atom_id == source.id
    assert created.atom_text == source.atom_text
    assert source.is_active is True  # ADD-only: source stays


async def test_accept_sender_address_widening_writes_domain_key(db_session) -> None:
    """sender_address → sender_domain must store domain:host, not the sender key."""
    from app.models.db.feedback_atom import FeedbackAtom

    source = FeedbackAtom(
        id=uuid.uuid4(),
        source_kind="preference_pair",
        source_id=uuid.uuid4(),
        mailbox=MAILBOX,
        atom_text="Always name the driver",
        atom_embedding=[0.0] * 1535 + [1.0],
        role="Fix",
        scope="sender_address",
        scope_key="sender:ops@statuspage.io",
        is_active=True,
    )
    db_session.add(source)
    await db_session.flush()
    proposal = PromotionProposal(
        id=uuid.uuid4(),
        mailbox=MAILBOX,
        kind="atom_widening",
        payload={"from_scope": "sender_address", "requested_scope": "sender_domain"},
        impact_num=3,
        impact_den=3,
        evidence_ids=[source.id],
        status="pending",
        expires_at=EXPIRES,
    )
    db_session.add(proposal)
    await db_session.flush()

    accepted = await accept_proposal(db_session, proposal.id, skip_gate=True)
    created_id = uuid.UUID(accepted.payload["created_atom_id"])
    created = (
        await db_session.execute(select(FeedbackAtom).where(FeedbackAtom.id == created_id))
    ).scalar_one()
    assert created.scope == "sender_domain"
    assert created.scope_key == "domain:statuspage.io"


async def test_accept_note_widening_updates_scope(db_session) -> None:
    from app.models.db.teaching_note import TeachingNote

    note = TeachingNote(
        id=uuid.uuid4(),
        mailbox=MAILBOX,
        title="Statuspage",
        body="Treat resolved incidents as LOW",
        scope="sender_domain",
        scope_key="domain:statuspage.io",
        status="active",
        origin="manual",
    )
    db_session.add(note)
    await db_session.flush()
    proposal = PromotionProposal(
        id=uuid.uuid4(),
        mailbox=MAILBOX,
        kind="note_widening",
        payload={
            "note_id": str(note.id),
            "current_scope": "sender_domain",
            "requested_scope": "mailbox",
        },
        impact_num=0,
        impact_den=0,
        evidence_ids=[note.id],
        status="pending",
        expires_at=EXPIRES,
    )
    db_session.add(proposal)
    await db_session.flush()

    accepted = await accept_proposal(db_session, proposal.id, skip_gate=True)
    assert accepted.payload["note_id"] == str(note.id)
    await db_session.refresh(note)
    assert note.scope == "mailbox"
    assert note.scope_key == f"mailbox:{MAILBOX}"


async def test_accept_non_pending_raises(db_session) -> None:
    """Accepting a non-pending proposal raises ProposalConflictError."""
    from app.core.exceptions import ProposalConflictError

    proposal_id = await _create_pending_proposal(db_session)
    await accept_proposal(db_session, proposal_id, skip_gate=True)

    # Proposal is now 'accepted' — trying to accept again should fail
    with pytest.raises(ProposalConflictError, match="not pending"):
        await accept_proposal(db_session, proposal_id, skip_gate=True)


async def test_revert_non_accepted_raises(db_session) -> None:
    """Reverting a pending (not yet accepted) proposal raises ProposalConflictError."""
    from app.core.exceptions import ProposalConflictError

    proposal_id = await _create_pending_proposal(db_session)

    with pytest.raises(ProposalConflictError, match="not accepted"):
        await revert_promotion(db_session, proposal_id)


async def test_revert_atom_widening_deactivates_created_only(db_session) -> None:
    from app.models.db.feedback_atom import FeedbackAtom

    source = FeedbackAtom(
        id=uuid.uuid4(),
        source_kind="preference_pair",
        source_id=uuid.uuid4(),
        mailbox=MAILBOX,
        atom_text="Always greet by first name",
        atom_embedding=[0.0] * 1535 + [1.0],
        role="Fix",
        scope="sender_domain",
        scope_key="domain:acme.com",
        is_active=True,
    )
    db_session.add(source)
    await db_session.flush()
    proposal = PromotionProposal(
        id=uuid.uuid4(),
        mailbox=MAILBOX,
        kind="atom_widening",
        payload={"from_scope": "sender_domain", "requested_scope": "mailbox"},
        impact_num=3,
        impact_den=3,
        evidence_ids=[source.id],
        status="pending",
        expires_at=EXPIRES,
    )
    db_session.add(proposal)
    await db_session.flush()

    accepted = await accept_proposal(db_session, proposal.id, skip_gate=True)
    created_id = uuid.UUID(accepted.payload["created_atom_id"])
    await revert_promotion(db_session, proposal.id)

    created = (
        await db_session.execute(select(FeedbackAtom).where(FeedbackAtom.id == created_id))
    ).scalar_one()
    await db_session.refresh(source)
    assert created.is_active is False
    assert source.is_active is True


async def test_revert_note_widening_restores_prior_scope(db_session) -> None:
    from app.models.db.teaching_note import TeachingNote

    note = TeachingNote(
        id=uuid.uuid4(),
        mailbox=MAILBOX,
        title="Statuspage",
        body="Treat resolved incidents as LOW",
        scope="sender_domain",
        scope_key="domain:statuspage.io",
        status="active",
        origin="manual",
    )
    db_session.add(note)
    await db_session.flush()
    proposal = PromotionProposal(
        id=uuid.uuid4(),
        mailbox=MAILBOX,
        kind="note_widening",
        payload={
            "note_id": str(note.id),
            "current_scope": "sender_domain",
            "requested_scope": "mailbox",
        },
        impact_num=0,
        impact_den=0,
        evidence_ids=[note.id],
        status="pending",
        expires_at=EXPIRES,
    )
    db_session.add(proposal)
    await db_session.flush()

    await accept_proposal(db_session, proposal.id, skip_gate=True)
    await revert_promotion(db_session, proposal.id)
    await db_session.refresh(note)
    assert note.scope == "sender_domain"
    assert note.scope_key == "domain:statuspage.io"


async def test_accept_high_replay_change_rate_raises(db_session) -> None:
    """2 of 5 traces would change (0.40 > 0.20) → accept raises without skip_gate."""
    from app.models.db.draft import Draft
    from app.models.db.golden_set_case import GoldenSetCase
    from app.models.db.thread import Thread
    from app.models.db.urgency_prediction import UrgencyPrediction

    db_session.add(
        GoldenSetCase(
            id=uuid.uuid4(),
            mailbox=MAILBOX,
            email_text="Subject: resolved incident from statuspage.io\n\nAll systems resolved.",
            expected_urgency="LOW",
            expected_action="no_action_discarded",
        )
    )
    await db_session.flush()
    thread = Thread(
        id=uuid.uuid4(),
        mailbox=MAILBOX,
        conversation_id=str(uuid.uuid4()),
        subject="replay",
        state="new",
    )
    db_session.add(thread)
    await db_session.flush()

    # 3 vendor.com HIGH (no match) + 2 statuspage.io HIGH (would become LOW)
    for domain in ["vendor.com", "vendor.com", "vendor.com", "statuspage.io", "statuspage.io"]:
        draft = Draft(
            id=uuid.uuid4(),
            thread_id=thread.id,
            message_id=str(uuid.uuid4()),
            subject="re: replay",
            body="body",
            recipients={},
            teaching_note="",
        )
        db_session.add(draft)
        await db_session.flush()
        db_session.add(
            UrgencyPrediction(
                id=uuid.uuid4(),
                draft_id=draft.id,
                thread_id=thread.id,
                mailbox=MAILBOX,
                sender_domain=domain,
                predicted_urgency="HIGH",
                probs={"LOW": 0.0, "NORMAL": 0.0, "HIGH": 1.0, "CRITICAL": 0.0},
                final_urgency="HIGH",
            )
        )
    await db_session.flush()

    proposal_id = await _create_pending_proposal(db_session)
    with pytest.raises(ValueError, match="change_rate"):
        await accept_proposal(db_session, proposal_id, skip_gate=False)
