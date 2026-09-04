"""DB tests for feedback_atom_service.propose_widenings.

Oracle (plan §4.1 / §8): a cluster of ≥3 similar active Fix atoms at a scope
narrower than mailbox proposes one atom_widening card. Mailbox/global atoms
are already wide and must not propose. Cross-mailbox rows never mix.

Worked example: 3 sender_address Fix atoms share the same embedding (cosine
distance 0). Threshold is 3 → exactly one proposal with impact_num=3.
"""

from __future__ import annotations

import uuid

import pytest

from app.repositories import feedback_atom_repo, promotion_proposal_repo
from app.services.feedback_atom_service import propose_widenings

pytestmark = pytest.mark.db

MAILBOX = "sales@example.com"
OTHER = "billing@example.com"


def _unit_vec(i: int) -> list[float]:
    v = [0.0] * 1536
    v[i % 1536] = 1.0
    return v


async def _insert_fix(
    session,
    *,
    mailbox: str,
    embedding: list[float],
    scope: str,
    scope_key: str,
    text: str = "Always name the driver in billing confirmations",
) -> None:
    await feedback_atom_repo.insert_atom(
        session,
        source_kind="preference_pair",
        source_id=uuid.uuid4(),
        mailbox=mailbox,
        atom_text=text,
        atom_embedding=embedding,
        role="Fix",
        scope=scope,
        scope_key=scope_key,
        person_bound=scope in {"thread", "sender_address"},
    )


@pytest.mark.asyncio
async def test_three_similar_narrow_fix_atoms_propose_one_widening(db_session) -> None:
    embedding = _unit_vec(7)
    for _ in range(3):
        await _insert_fix(
            db_session,
            mailbox=MAILBOX,
            embedding=embedding,
            scope="sender_address",
            scope_key="sender:ops@statuspage.io",
        )
    await db_session.commit()

    proposals = await propose_widenings(db_session, MAILBOX)
    await db_session.commit()

    assert len(proposals) == 1
    assert proposals[0].kind == "atom_widening"
    assert proposals[0].mailbox == MAILBOX
    assert proposals[0].impact_num == 3  # hand-counted cluster size
    assert proposals[0].impact_den == 3
    assert len(proposals[0].evidence_ids) == 3
    assert proposals[0].payload["requested_scope"] == "sender_domain"
    assert proposals[0].payload["requested_scope_key"] == "domain:statuspage.io"
    assert proposals[0].payload["person_bound"] is True

    pending = await promotion_proposal_repo.list_pending_proposals(db_session, mailbox=MAILBOX)
    assert len(pending) == 1


@pytest.mark.asyncio
async def test_two_similar_atoms_do_not_propose(db_session) -> None:
    embedding = _unit_vec(11)
    for _ in range(2):
        await _insert_fix(
            db_session,
            mailbox=MAILBOX,
            embedding=embedding,
            scope="sender_domain",
            scope_key="domain:statuspage.io",
        )
    await db_session.commit()

    proposals = await propose_widenings(db_session, MAILBOX)
    assert proposals == []


@pytest.mark.asyncio
async def test_mailbox_scoped_atoms_are_not_proposed(db_session) -> None:
    embedding = _unit_vec(13)
    for _ in range(3):
        await _insert_fix(
            db_session,
            mailbox=MAILBOX,
            embedding=embedding,
            scope="mailbox",
            scope_key=f"mailbox:{MAILBOX}",
        )
    await db_session.commit()

    proposals = await propose_widenings(db_session, MAILBOX)
    assert proposals == []


@pytest.mark.asyncio
async def test_other_mailbox_atoms_do_not_count(db_session) -> None:
    embedding = _unit_vec(17)
    for _ in range(3):
        await _insert_fix(
            db_session,
            mailbox=OTHER,
            embedding=embedding,
            scope="sender_address",
            scope_key="sender:ops@statuspage.io",
        )
    await db_session.commit()

    proposals = await propose_widenings(db_session, MAILBOX)
    assert proposals == []


@pytest.mark.asyncio
async def test_orthogonal_embeddings_do_not_cluster(db_session) -> None:
    """Three Fix atoms on orthogonal unit vectors (cosine distance 1.0) stay unclustered."""
    for i in range(3):
        await _insert_fix(
            db_session,
            mailbox=MAILBOX,
            embedding=_unit_vec(i),
            scope="sender_domain",
            scope_key="domain:statuspage.io",
        )
    await db_session.commit()

    proposals = await propose_widenings(db_session, MAILBOX)
    assert proposals == []
