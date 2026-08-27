"""Behavioral oracles for high-precision drip association + bidirectional confirm.

Worked examples (hand-counted, independent of production helpers):
- SampleClient 8/9 vs 8/14 (same sender, date-stripped subject) -> associate
- January invoice from same sender -> stay out
- Daily Drivers x3 (identical subject) -> each sees the other two
- Confirm on one side with only a one-way proposal -> both threads show confirmed
- Propose never bumps urgency and never revives dismiss / demotes confirm
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.core.exceptions import ThreadStateError
from app.models.db.audit_event import AuditEvent
from app.models.db.message import Message
from app.models.db.thread import Thread
from app.models.schemas.email import ThreadStateEnum
from app.repositories import association_review_repo

pytestmark = pytest.mark.db

SALES = "sales@example.com"
SUPPORT = "support@example.com"
SENDER = "rep@sample-client.example.com"
OTHER_SENDER = "billing@vendor.com"
ACCOUNTING = "accounting@sample-services.example.com"
T_SRC = datetime(2026, 8, 9, 14, 0, tzinfo=UTC)
T_SIB = datetime(2026, 8, 14, 15, 0, tzinfo=UTC)
T_INV = datetime(2026, 8, 14, 16, 0, tzinfo=UTC)
T_OLD = T_SIB - timedelta(days=100)


def _settings(*, mailboxes: str = SALES) -> Settings:
    return Settings(
        environment="local",
        jwt_secret="c" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        target_mailboxes=mailboxes,
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
    )


def _thread(
    *,
    conversation_id: str,
    subject: str,
    last_message_at: datetime,
    mailbox: str = SALES,
    urgency: str = "NORMAL",
) -> Thread:
    return Thread(
        id=uuid.uuid4(),
        mailbox=mailbox,
        conversation_id=conversation_id,
        subject=subject,
        state=ThreadStateEnum.DRAFTED.value,
        urgency=urgency,
        last_message_at=last_message_at,
    )


def _message(
    thread: Thread,
    *,
    received_at: datetime,
    sender: str = SENDER,
    direction: str = "inbound",
) -> Message:
    return Message(
        id=uuid.uuid4(),
        thread_id=thread.id,
        graph_message_id=str(uuid.uuid4()),
        direction=direction,
        sender=sender,
        body_text="body",
        received_at=received_at,
        to_recipients=[],
        cc_recipients=[],
    )


async def _seed_sampleclient(session) -> dict[str, Thread]:
    source = _thread(
        conversation_id="sampleclient-8-9",
        subject="SampleClient follow-up 8/9",
        last_message_at=T_SRC,
    )
    sibling = _thread(
        conversation_id="sampleclient-8-14",
        subject="SampleClient follow-up 8/14",
        last_message_at=T_SIB,
    )
    invoice = _thread(
        conversation_id="january-invoice",
        subject="January invoice",
        last_message_at=T_INV,
    )
    session.add_all([source, sibling, invoice])
    await session.flush()
    session.add_all(
        [
            _message(source, received_at=T_SRC),
            _message(sibling, received_at=T_SIB),
            _message(invoice, received_at=T_INV),
        ]
    )
    await session.commit()
    return {"source": source, "sibling": sibling, "invoice": invoice}


@pytest.mark.asyncio
async def test_propose_drip_links_same_sender_date_stripped_subject_both_ways(
    db_session,
) -> None:
    """Break: date-stripped match skipped or only written one direction."""
    rows = await _seed_sampleclient(db_session)
    source = rows["source"]
    sibling = rows["sibling"]
    from app.services import related_thread_service

    proposed = await related_thread_service.propose_drip_associations(
        db_session,
        thread_id=source.id,
        now=T_SIB,
    )
    await db_session.commit()

    assert proposed == [sibling.id]

    from_source = await association_review_repo.statuses_for_source(db_session, source.id)
    from_sibling = await association_review_repo.statuses_for_source(db_session, sibling.id)
    assert from_source[sibling.id] == "proposed"
    assert from_sibling[source.id] == "proposed"


@pytest.mark.asyncio
async def test_propose_drip_excludes_different_subject_same_sender(db_session) -> None:
    """Break: same sender alone is enough to associate (invoice false positive)."""
    rows = await _seed_sampleclient(db_session)
    source = rows["source"]
    invoice = rows["invoice"]
    from app.services import related_thread_service

    proposed = await related_thread_service.propose_drip_associations(
        db_session,
        thread_id=source.id,
        now=T_SIB,
    )
    await db_session.commit()

    assert invoice.id not in proposed
    statuses = await association_review_repo.statuses_for_source(db_session, source.id)
    assert invoice.id not in statuses


@pytest.mark.asyncio
async def test_propose_drip_excludes_different_sender_same_subject(db_session) -> None:
    """Break: subject match alone associates across senders."""
    source = _thread(
        conversation_id="hc-a",
        subject="SampleClient follow-up 8/9",
        last_message_at=T_SRC,
    )
    other = _thread(
        conversation_id="hc-b",
        subject="SampleClient follow-up 8/14",
        last_message_at=T_SIB,
    )
    db_session.add_all([source, other])
    await db_session.flush()
    db_session.add_all(
        [
            _message(source, received_at=T_SRC, sender=SENDER),
            _message(other, received_at=T_SIB, sender=OTHER_SENDER),
        ]
    )
    await db_session.commit()
    from app.services import related_thread_service

    proposed = await related_thread_service.propose_drip_associations(
        db_session,
        thread_id=source.id,
        now=T_SIB,
    )
    await db_session.commit()

    assert proposed == []
    statuses = await association_review_repo.statuses_for_source(db_session, source.id)
    assert other.id not in statuses


@pytest.mark.asyncio
async def test_propose_drip_stays_inside_one_mailbox(db_session) -> None:
    """Break: same sender/subject in another mailbox is linked (tenant bleed)."""
    source = _thread(
        conversation_id="sales-hc",
        subject="SampleClient follow-up 8/9",
        last_message_at=T_SRC,
        mailbox=SALES,
    )
    other_box = _thread(
        conversation_id="support-hc",
        subject="SampleClient follow-up 8/14",
        last_message_at=T_SIB,
        mailbox=SUPPORT,
    )
    db_session.add_all([source, other_box])
    await db_session.flush()
    db_session.add_all(
        [
            _message(source, received_at=T_SRC),
            _message(other_box, received_at=T_SIB),
        ]
    )
    await db_session.commit()
    from app.services import related_thread_service

    proposed = await related_thread_service.propose_drip_associations(
        db_session,
        thread_id=source.id,
        now=T_SIB,
    )
    await db_session.commit()

    assert proposed == []
    statuses = await association_review_repo.statuses_for_source(db_session, source.id)
    assert other_box.id not in statuses


@pytest.mark.asyncio
async def test_propose_drip_excludes_siblings_older_than_90_days(db_session) -> None:
    """Break: window is ignored so stale drips keep associating."""
    source = _thread(
        conversation_id="hc-new",
        subject="SampleClient follow-up 8/14",
        last_message_at=T_SIB,
    )
    stale = _thread(
        conversation_id="hc-old",
        subject="SampleClient follow-up 8/9",
        last_message_at=T_OLD,
    )
    db_session.add_all([source, stale])
    await db_session.flush()
    db_session.add_all(
        [
            _message(source, received_at=T_SIB),
            _message(stale, received_at=T_OLD),
        ]
    )
    await db_session.commit()
    from app.services import related_thread_service

    proposed = await related_thread_service.propose_drip_associations(
        db_session,
        thread_id=source.id,
        now=T_SIB,
    )
    await db_session.commit()

    assert proposed == []
    statuses = await association_review_repo.statuses_for_source(db_session, source.id)
    assert stale.id not in statuses


@pytest.mark.asyncio
async def test_propose_drip_daily_drivers_cluster_links_all_three(db_session) -> None:
    """Break: identical-subject human drips never associate (Daily Drivers gap).

    Three open drips, same mailbox/sender/subject. Propose from the newest:
    both older siblings are proposed both ways. Invoice-shaped third party stays out.
    """
    t1 = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    t2 = datetime(2026, 3, 8, 12, 0, tzinfo=UTC)
    t3 = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)
    a = _thread(
        conversation_id="dd-1",
        subject="Daily Drivers changes update.",
        last_message_at=t1,
        mailbox=SUPPORT,
    )
    b = _thread(
        conversation_id="dd-2",
        subject="Daily Drivers changes update.",
        last_message_at=t2,
        mailbox=SUPPORT,
    )
    c = _thread(
        conversation_id="dd-3",
        subject="Daily Drivers changes update.",
        last_message_at=t3,
        mailbox=SUPPORT,
    )
    noise = _thread(
        conversation_id="other-acct",
        subject="W-9 request",
        last_message_at=t3,
        mailbox=SUPPORT,
    )
    db_session.add_all([a, b, c, noise])
    await db_session.flush()
    db_session.add_all(
        [
            _message(a, received_at=t1, sender=ACCOUNTING),
            _message(b, received_at=t2, sender=ACCOUNTING),
            _message(c, received_at=t3, sender=ACCOUNTING),
            _message(noise, received_at=t3, sender=ACCOUNTING),
        ]
    )
    await db_session.commit()
    from app.services import related_thread_service

    proposed = await related_thread_service.propose_drip_associations(
        db_session,
        thread_id=c.id,
        now=t3,
    )
    await db_session.commit()

    assert set(proposed) == {a.id, b.id}
    assert noise.id not in proposed

    from_c = await association_review_repo.statuses_for_source(db_session, c.id)
    from_a = await association_review_repo.statuses_for_source(db_session, a.id)
    from_b = await association_review_repo.statuses_for_source(db_session, b.id)
    assert from_c[a.id] == "proposed"
    assert from_c[b.id] == "proposed"
    assert from_a[c.id] == "proposed"
    assert from_b[c.id] == "proposed"
    assert noise.id not in from_c


@pytest.mark.asyncio
async def test_propose_drip_does_not_revive_dismissed_pair(db_session) -> None:
    """Break: re-propose overwrites human Remove."""
    rows = await _seed_sampleclient(db_session)
    source = rows["source"]
    sibling = rows["sibling"]
    await association_review_repo.set_status(
        db_session,
        source_thread_id=source.id,
        related_thread_id=sibling.id,
        status="dismissed",
        actor="elise@example.com",
    )
    await association_review_repo.set_status(
        db_session,
        source_thread_id=sibling.id,
        related_thread_id=source.id,
        status="dismissed",
        actor="elise@example.com",
    )
    await db_session.commit()

    from app.services import related_thread_service

    proposed = await related_thread_service.propose_drip_associations(
        db_session,
        thread_id=source.id,
        now=T_SIB,
    )
    await db_session.commit()

    assert sibling.id not in proposed
    statuses = await association_review_repo.statuses_for_source(db_session, source.id)
    assert statuses[sibling.id] == "dismissed"


@pytest.mark.asyncio
async def test_propose_drip_does_not_demote_confirmed_pair(db_session) -> None:
    """Break: re-propose turns confirmed back into proposed."""
    rows = await _seed_sampleclient(db_session)
    source = rows["source"]
    sibling = rows["sibling"]
    await association_review_repo.set_status(
        db_session,
        source_thread_id=source.id,
        related_thread_id=sibling.id,
        status="confirmed",
        actor="elise@example.com",
    )
    await association_review_repo.set_status(
        db_session,
        source_thread_id=sibling.id,
        related_thread_id=source.id,
        status="confirmed",
        actor="elise@example.com",
    )
    await db_session.commit()

    from app.services import related_thread_service

    proposed = await related_thread_service.propose_drip_associations(
        db_session,
        thread_id=source.id,
        now=T_SIB,
    )
    await db_session.commit()

    assert sibling.id not in proposed
    from_source = await association_review_repo.statuses_for_source(db_session, source.id)
    from_sibling = await association_review_repo.statuses_for_source(db_session, sibling.id)
    assert from_source[sibling.id] == "confirmed"
    assert from_sibling[source.id] == "confirmed"


@pytest.mark.asyncio
async def test_confirm_with_only_one_way_proposal_shows_on_both_threads(
    db_session,
) -> None:
    """Break: Confirm writes only the clicked direction (Daily Drivers reverse empty).

    Legacy / partial state: A→B proposed, B→A missing. Confirm from A must leave
    both thread pages showing the other as confirmed.
    """
    rows = await _seed_sampleclient(db_session)
    source = rows["source"]
    sibling = rows["sibling"]
    await association_review_repo.upsert_proposed(
        db_session,
        source_thread_id=source.id,
        related_thread_id=sibling.id,
        score=1.0,
    )
    await db_session.commit()

    reverse_before = await association_review_repo.statuses_for_source(db_session, sibling.id)
    assert source.id not in reverse_before

    from app.services import related_thread_service

    result = await related_thread_service.review_related(
        db_session,
        _settings(),
        source.id,
        sibling.id,
        status="confirmed",
        actor="elise@example.com",
    )
    await db_session.commit()

    assert result.status == "confirmed"
    from_source = await related_thread_service.list_stored_associations(db_session, source.id)
    from_sibling = await related_thread_service.list_stored_associations(db_session, sibling.id)
    assert len(from_source) == 1
    assert from_source[0].thread_id == sibling.id
    assert from_source[0].status == "confirmed"
    assert len(from_sibling) == 1
    assert from_sibling[0].thread_id == source.id
    assert from_sibling[0].status == "confirmed"


@pytest.mark.asyncio
async def test_confirm_without_any_proposal_is_rejected(db_session) -> None:
    """Break: Confirm invents associations with no prior propose."""
    rows = await _seed_sampleclient(db_session)
    from app.services import related_thread_service

    with pytest.raises(ThreadStateError):
        await related_thread_service.review_related(
            db_session,
            _settings(),
            rows["source"].id,
            rows["sibling"].id,
            status="confirmed",
            actor="elise@example.com",
        )


@pytest.mark.asyncio
async def test_propose_drip_does_not_change_urgency_or_emit_escalation(
    db_session,
) -> None:
    """Break: drip propose reuses alert recurrence floors (FYI → HIGH/CRITICAL)."""
    rows = await _seed_sampleclient(db_session)
    source = rows["source"]
    sibling = rows["sibling"]
    from app.services import related_thread_service

    await related_thread_service.propose_drip_associations(
        db_session,
        thread_id=source.id,
        now=T_SIB,
    )
    await db_session.commit()

    refreshed_source = (
        await db_session.execute(select(Thread).where(Thread.id == source.id))
    ).scalar_one()
    refreshed_sibling = (
        await db_session.execute(select(Thread).where(Thread.id == sibling.id))
    ).scalar_one()
    assert refreshed_source.urgency == "NORMAL"
    assert refreshed_sibling.urgency == "NORMAL"

    escalated = (
        (
            await db_session.execute(
                select(AuditEvent).where(
                    AuditEvent.event_type == "thread.urgency.recurrence_escalated",
                    AuditEvent.mailbox == SALES,
                )
            )
        )
        .scalars()
        .all()
    )
    assert escalated == []


@pytest.mark.asyncio
async def test_propose_drip_without_messages_proposes_nothing(db_session) -> None:
    """Break: empty threads invent associations from subject alone."""
    source = _thread(
        conversation_id="no-messages",
        subject="SampleClient follow-up 8/9",
        last_message_at=T_SRC,
    )
    sibling = _thread(
        conversation_id="has-inbound",
        subject="SampleClient follow-up 8/14",
        last_message_at=T_SIB,
    )
    db_session.add_all([source, sibling])
    await db_session.flush()
    db_session.add(_message(sibling, received_at=T_SIB))
    await db_session.commit()
    from app.services import related_thread_service

    proposed = await related_thread_service.propose_drip_associations(
        db_session,
        thread_id=source.id,
        now=T_SIB,
    )
    await db_session.commit()

    assert proposed == []
    statuses = await association_review_repo.statuses_for_source(db_session, source.id)
    assert statuses == {}


@pytest.mark.asyncio
async def test_propose_drip_idempotent_when_already_proposed(db_session) -> None:
    """Break: second propose returns duplicates or mutates status."""
    rows = await _seed_sampleclient(db_session)
    source = rows["source"]
    sibling = rows["sibling"]
    from app.services import related_thread_service

    first = await related_thread_service.propose_drip_associations(
        db_session,
        thread_id=source.id,
        now=T_SIB,
    )
    await db_session.commit()
    second = await related_thread_service.propose_drip_associations(
        db_session,
        thread_id=source.id,
        now=T_SIB,
    )
    await db_session.commit()

    assert first == [sibling.id]
    assert second == []
    statuses = await association_review_repo.statuses_for_source(db_session, source.id)
    assert statuses[sibling.id] == "proposed"
