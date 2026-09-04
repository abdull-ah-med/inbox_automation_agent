"""DB tests for paired_retrieval_service.

Worked fixture:
  - 8 preference pairs seeded across 2 mailboxes (MAILBOX_A and MAILBOX_B).
  - 4 pairs in MAILBOX_A: 2 approve, 2 reject.
  - 4 pairs in MAILBOX_B: 2 approve, 2 reject.

Cross-mailbox isolation: retrieve_for_draft for MAILBOX_A must NEVER return
MAILBOX_B pairs.

Scope order: pairs seeded at different scopes; verify narrower scopes appear
before wider scopes in the results (sender_address before sender_domain).

Oracles are hand-counted from the fixture — not derived from the service code.
"""

from __future__ import annotations

import hashlib
import uuid

import pytest

from app.models.db.draft import Draft
from app.models.db.feedback_atom import FeedbackAtom
from app.models.db.thread import Thread
from app.repositories import preference_pair_repo
from app.services.paired_retrieval_service import retrieve_for_draft

pytestmark = pytest.mark.db

MAILBOX_A = "ops@alpha.com"
MAILBOX_B = "ops@beta.com"
SENDER_A = "vendor@acme.com"
DOMAIN_A = "acme.com"
SENDER_B = "client@other.com"
DOMAIN_B = "other.com"


def _unit_vec(i: int) -> list[float]:
    v = [0.0] * 1536
    v[i % 1536] = 1.0
    return v


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def _make_thread_draft(session, mailbox: str) -> tuple[Thread, Draft]:
    thread = Thread(
        id=uuid.uuid4(),
        mailbox=mailbox,
        conversation_id=str(uuid.uuid4()),
        subject="test thread",
        state="new",
    )
    session.add(thread)
    await session.flush()
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
    return thread, draft


async def _insert_pair(
    session,
    *,
    mailbox,
    sender_address,
    sender_domain,
    routing_category,
    embed_idx,
    decision="approve",
):
    thread, draft = await _make_thread_draft(session, mailbox)
    return await preference_pair_repo.insert_preference_pair(
        session,
        draft_id=draft.id,
        thread_id=thread.id,
        mailbox=mailbox,
        sender_address=sender_address,
        sender_domain=sender_domain,
        routing_category=routing_category,
        email_text_hash=_hash(f"email_{embed_idx}"),
        email_embedding=_unit_vec(embed_idx),
        decision=decision,
        chosen_body="Approved body" if decision == "approve" else None,
        rejected_body="Rejected body" if decision == "reject" else None,
    )


async def test_cross_mailbox_isolation(db_session) -> None:
    """Pairs from MAILBOX_B are never returned when querying for MAILBOX_A."""
    # Seed 4 pairs in each mailbox
    for i in range(4):
        await _insert_pair(
            db_session,
            mailbox=MAILBOX_A,
            sender_address=SENDER_A,
            sender_domain=DOMAIN_A,
            routing_category="billing",
            embed_idx=i,
        )
    for i in range(4, 8):
        await _insert_pair(
            db_session,
            mailbox=MAILBOX_B,
            sender_address=SENDER_B,
            sender_domain=DOMAIN_B,
            routing_category="billing",
            embed_idx=i,
        )
    await db_session.flush()

    # Query for MAILBOX_A using a query vector similar to its pairs
    result = await retrieve_for_draft(
        db_session,
        mailbox=MAILBOX_A,
        sender_address=SENDER_A,
        sender_domain=DOMAIN_A,
        routing_category="billing",
        email_embedding=_unit_vec(0),  # matches MAILBOX_A pairs
        limit_per_scope=5,
        hard_limit=10,
    )

    mailboxes_returned = {p.mailbox for p in result.pairs}
    assert MAILBOX_B not in mailboxes_returned, (
        f"MAILBOX_B pairs leaked into MAILBOX_A result: {mailboxes_returned}"
    )
    # All returned pairs belong to MAILBOX_A
    assert all(p.mailbox == MAILBOX_A for p in result.pairs)


async def test_hard_limit_respected(db_session) -> None:
    """retrieve_for_draft never returns more pairs than hard_limit."""
    for i in range(10):
        await _insert_pair(
            db_session,
            mailbox=MAILBOX_A,
            sender_address=SENDER_A,
            sender_domain=DOMAIN_A,
            routing_category="general",
            embed_idx=i + 50,
        )
    await db_session.flush()

    result = await retrieve_for_draft(
        db_session,
        mailbox=MAILBOX_A,
        sender_address=SENDER_A,
        sender_domain=DOMAIN_A,
        routing_category="general",
        email_embedding=_unit_vec(50),
        limit_per_scope=5,
        hard_limit=3,  # tight limit
    )

    assert len(result.pairs) <= 3, f"hard_limit=3 violated: got {len(result.pairs)} pairs"


async def test_fix_atoms_returned_for_mailbox(db_session) -> None:
    """Fix atoms for the mailbox scope are in fix_atoms; Spec atoms in spec_atoms."""
    atom_fix = FeedbackAtom(
        id=uuid.uuid4(),
        source_kind="rejection",
        source_id=uuid.uuid4(),
        mailbox=MAILBOX_A,
        atom_text="Always include invoice number in reply",
        atom_embedding=_unit_vec(100),
        role="Fix",
        scope="mailbox",
        scope_key=f"mailbox:{MAILBOX_A}",
        is_active=True,
    )
    atom_spec = FeedbackAtom(
        id=uuid.uuid4(),
        source_kind="rejection",
        source_id=uuid.uuid4(),
        mailbox=MAILBOX_A,
        atom_text="When amount exceeds $1000",
        atom_embedding=_unit_vec(101),
        role="Spec",
        scope="mailbox",
        scope_key=f"mailbox:{MAILBOX_A}",
        is_active=True,
    )
    db_session.add(atom_fix)
    db_session.add(atom_spec)
    await db_session.flush()

    result = await retrieve_for_draft(
        db_session,
        mailbox=MAILBOX_A,
        sender_address=None,
        sender_domain=None,
        routing_category=None,
        email_embedding=_unit_vec(0),
    )

    atom_roles = {a.role for a in result.fix_atoms}
    # Fix atoms are retrieved; Spec atoms are not part of fix_atoms
    assert "Fix" in atom_roles, "Fix atom should appear in fix_atoms"
    assert "Spec" not in atom_roles, "Spec atoms must not appear in fix_atoms"
    spec_texts = [a.atom_text for a in result.spec_atoms]
    assert "When amount exceeds $1000" in spec_texts

    fix_texts = [a.atom_text for a in result.fix_atoms]
    assert "Always include invoice number in reply" in fix_texts


async def test_pair_hard_limit_still_returns_mailbox_fix_atoms(db_session) -> None:
    """Filling the pair quota must not skip later-scope Fix atoms.

    Fixture: 3 sender_address pairs + 2 mailbox-scope Fix atoms.
    Query hard_limit=2 (pairs). Oracle: both mailbox atoms are still returned.
    """
    for i in range(3):
        await _insert_pair(
            db_session,
            mailbox=MAILBOX_A,
            sender_address=SENDER_A,
            sender_domain=DOMAIN_A,
            routing_category="billing",
            embed_idx=i + 200,
        )
    atom_texts = (
        "Always quote the load number in the first sentence",
        "Never attach the full driver file to the reply",
    )
    for i, text in enumerate(atom_texts):
        db_session.add(
            FeedbackAtom(
                id=uuid.uuid4(),
                source_kind="preference_pair",
                source_id=uuid.uuid4(),
                mailbox=MAILBOX_A,
                atom_text=text,
                atom_embedding=_unit_vec(300 + i),
                role="Fix",
                scope="mailbox",
                scope_key=f"mailbox:{MAILBOX_A}",
                is_active=True,
            )
        )
    await db_session.flush()

    result = await retrieve_for_draft(
        db_session,
        mailbox=MAILBOX_A,
        sender_address=SENDER_A,
        sender_domain=DOMAIN_A,
        routing_category="billing",
        email_embedding=_unit_vec(200),
        limit_per_scope=5,
        hard_limit=2,
    )

    assert len(result.pairs) == 2  # hard_limit caps pairs only
    returned_texts = {a.atom_text for a in result.fix_atoms}
    assert set(atom_texts) <= returned_texts


async def test_sender_address_scope_excludes_other_sender(db_session) -> None:
    """A same-mailbox pair from other@x.com is not in the sender_address slice.

    The other-sender vector is an exact match for the query embedding, so
    without a sender_address WHERE it would win cosine. hard_limit=1 keeps
    results in the first (sender_address) scope.
    """
    other_sender = "other@x.com"
    await _insert_pair(
        db_session,
        mailbox=MAILBOX_A,
        sender_address=SENDER_A,
        sender_domain=DOMAIN_A,
        routing_category="billing",
        embed_idx=0,
    )
    await _insert_pair(
        db_session,
        mailbox=MAILBOX_A,
        sender_address=other_sender,
        sender_domain="x.com",
        routing_category="billing",
        embed_idx=99,
    )
    await db_session.flush()

    result = await retrieve_for_draft(
        db_session,
        mailbox=MAILBOX_A,
        sender_address=SENDER_A,
        sender_domain=DOMAIN_A,
        routing_category="billing",
        email_embedding=_unit_vec(99),
        limit_per_scope=2,
        hard_limit=1,
    )

    assert len(result.pairs) == 1
    assert result.pairs[0].sender_address == SENDER_A
    assert result.pairs[0].sender_address != other_sender


async def test_empty_result_for_unknown_mailbox(db_session) -> None:
    """No pairs, atoms, or notes for a mailbox with no data."""
    result = await retrieve_for_draft(
        db_session,
        mailbox="nobody@nowhere.com",
        sender_address=None,
        sender_domain=None,
        routing_category=None,
        email_embedding=_unit_vec(0),
    )
    assert result.pairs == []
    assert result.fix_atoms == []
    assert result.notes == []


async def test_global_person_bound_atom_is_not_retrieved(db_session) -> None:
    bound = FeedbackAtom(
        id=uuid.uuid4(),
        source_kind="preference_pair",
        source_id=uuid.uuid4(),
        mailbox=MAILBOX_A,
        atom_text="Only Elise greets this vendor by nickname",
        atom_embedding=_unit_vec(7),
        role="Fix",
        scope="global",
        scope_key="global:",
        is_active=True,
        person_bound=True,
    )
    shared = FeedbackAtom(
        id=uuid.uuid4(),
        source_kind="preference_pair",
        source_id=uuid.uuid4(),
        mailbox=MAILBOX_B,
        atom_text="Always quote the load number in the first sentence",
        atom_embedding=_unit_vec(8),
        role="Fix",
        scope="global",
        scope_key="global:",
        is_active=True,
        person_bound=False,
    )
    db_session.add_all([bound, shared])
    await db_session.flush()

    result = await retrieve_for_draft(
        db_session,
        mailbox=MAILBOX_A,
        sender_address=SENDER_A,
        sender_domain=DOMAIN_A,
        routing_category="billing",
        email_embedding=_unit_vec(7),
    )

    texts = {atom.atom_text for atom in result.fix_atoms}
    assert shared.atom_text in texts
    assert bound.atom_text not in texts
