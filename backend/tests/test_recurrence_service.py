"""Cross-thread alert cluster and automatic urgency floor oracles.

Mailbox sales@example.com. All mail from noreply@ops.example (automated).
Worked times are wall-clock literals; expected urgencies are HIGH/CRITICAL
from the locked 2/3 floor, not from the implementation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from app.core.exceptions import ThreadStateError
from app.core.tenant_scope import TenantScope
from app.models.db.draft import Draft
from app.models.db.message import Message
from app.models.db.thread import Thread
from app.models.schemas.email import ThreadStateEnum
from app.repositories import audit_repo, thread_repo
from app.services import recurrence_service, related_thread_service
from app.services.related_match import alert_cluster_keys

pytestmark = pytest.mark.db

SALES = "sales@example.com"
ALERTS = "noreply@ops.example"
DISK_90 = "ALERT: Disk 90% on db-1"
DISK_91 = "ALERT: Disk 91% on db-1"
T0 = datetime(2026, 8, 20, 10, 0, tzinfo=UTC)
T_B = datetime(2026, 8, 20, 14, 0, tzinfo=UTC)
T_C = datetime(2026, 8, 21, 9, 0, tzinfo=UTC)
T_D = datetime(2026, 8, 21, 16, 0, tzinfo=UTC)
BUMP_2 = (
    "Urgency bumped automatically: 2 similar alerts in 48h "
    "(same sender and subject). Urgency raised to HIGH."
)
BUMP_3 = (
    "Urgency bumped automatically: 3 similar alerts in 48h "
    "(same sender and subject). Urgency raised to CRITICAL."
)


def _thread(
    *,
    conversation_id: str,
    subject: str,
    last_message_at: datetime,
    state: str = ThreadStateEnum.DRAFTED.value,
    urgency: str = "NORMAL",
) -> Thread:
    keys = alert_cluster_keys(mailbox=SALES, sender=ALERTS, subject=subject)
    fingerprint, signature, sender_norm = keys if keys else (None, None, None)
    return Thread(
        id=uuid.uuid4(),
        mailbox=SALES,
        conversation_id=conversation_id,
        subject=subject,
        state=state,
        urgency=urgency,
        last_message_at=last_message_at,
        alert_fingerprint=fingerprint,
        alert_signature=signature,
        alert_sender_norm=sender_norm,
    )


def _message(thread: Thread, *, received_at: datetime, sender: str = ALERTS) -> Message:
    return Message(
        id=uuid.uuid4(),
        thread_id=thread.id,
        graph_message_id=str(uuid.uuid4()),
        direction="inbound",
        sender=sender,
        body_text="disk alert",
        received_at=received_at,
        to_recipients=[],
        cc_recipients=[],
    )


def _draft(thread: Thread, *, created_at: datetime, urgency: str = "NORMAL") -> Draft:
    return Draft(
        id=uuid.uuid4(),
        thread_id=thread.id,
        message_id=str(uuid.uuid4()),
        subject=thread.subject,
        body="draft",
        recipients={},
        teaching_note="note",
        created_at=created_at,
        urgency=urgency,
    )


async def _persist(session, *rows) -> None:
    """Insert threads before FK children so Postgres sees parents first."""
    threads = [row for row in rows if isinstance(row, Thread)]
    children = [row for row in rows if not isinstance(row, Thread)]
    if threads:
        session.add_all(threads)
        await session.flush()
    if children:
        session.add_all(children)
        await session.flush()


async def _escalate(session, thread: Thread, *, assessed: str, now: datetime) -> str | None:
    return await recurrence_service.apply_recurrence_escalation(
        session,
        thread_id=thread.id,
        conversation_id=thread.conversation_id,
        mailbox=SALES,
        assessed_urgency=assessed,
        now=now,
    )


@pytest.mark.asyncio
async def test_pg_trgm_disk_90_vs_91_is_near_invoice_is_not(db_session) -> None:
    near = await db_session.execute(
        text("SELECT similarity(:a, :b)"),
        {"a": "disk 90% on db-1", "b": "disk 91% on db-1"},
    )
    far = await db_session.execute(
        text("SELECT similarity(:a, :b)"),
        {"a": "disk 90% on db-1", "b": "january invoice"},
    )
    assert near.scalar_one() >= 0.55
    assert far.scalar_one() < 0.55


@pytest.mark.asyncio
async def test_single_alert_stays_at_assessed_normal(db_session) -> None:
    thread_a = _thread(conversation_id="alert-a", subject=DISK_90, last_message_at=T0)
    await _persist(
        db_session,
        thread_a,
        _message(thread_a, received_at=T0),
        _draft(thread_a, created_at=T0),
    )
    await db_session.commit()

    applied = await _escalate(db_session, thread_a, assessed="NORMAL", now=T0)
    await db_session.commit()

    fresh = await thread_repo.get_by_id(
        db_session, thread_a.id, TenantScope.single(thread_a.mailbox)
    )
    assert applied == "NORMAL"
    assert fresh is not None
    assert fresh.urgency == "NORMAL"


@pytest.mark.asyncio
async def test_second_fingerprint_thread_floors_both_high(db_session) -> None:
    thread_a = _thread(conversation_id="alert-a", subject=DISK_90, last_message_at=T0)
    thread_b = _thread(conversation_id="alert-b", subject=DISK_90, last_message_at=T_B)
    await _persist(
        db_session,
        thread_a,
        thread_b,
        _message(thread_a, received_at=T0),
        _message(thread_b, received_at=T_B),
        _draft(thread_a, created_at=T0),
        _draft(thread_b, created_at=T_B),
    )
    await db_session.commit()

    await _escalate(db_session, thread_a, assessed="NORMAL", now=T0)
    applied_b = await _escalate(db_session, thread_b, assessed="NORMAL", now=T_B)
    await db_session.commit()

    a = await thread_repo.get_by_id(db_session, thread_a.id, TenantScope.single(thread_a.mailbox))
    b = await thread_repo.get_by_id(db_session, thread_b.id, TenantScope.single(thread_b.mailbox))
    assert applied_b == "HIGH"
    assert a is not None and b is not None
    assert a.urgency == "HIGH"
    assert b.urgency == "HIGH"
    assert a.urgency_reason == BUMP_2
    assert b.urgency_reason == BUMP_2


@pytest.mark.asyncio
async def test_informational_automated_cluster_does_not_floor_urgency(db_session) -> None:
    """Domain-auth style repeats: triage has_action_items=False → no CRITICAL bump."""
    from app.models.db.audit_event import AuditEvent

    subject = "Your domain is now authenticated"
    thread_a = _thread(
        conversation_id="domain-a", subject=subject, last_message_at=T0, urgency="LOW"
    )
    thread_b = _thread(
        conversation_id="domain-b", subject=subject, last_message_at=T_B, urgency="LOW"
    )
    thread_c = _thread(
        conversation_id="domain-c", subject=subject, last_message_at=T_C, urgency="LOW"
    )
    await _persist(
        db_session,
        thread_a,
        thread_b,
        thread_c,
        _message(thread_a, received_at=T0),
        _message(thread_b, received_at=T_B),
        _message(thread_c, received_at=T_C),
        _draft(thread_a, created_at=T0),
        _draft(thread_b, created_at=T_B),
        _draft(thread_c, created_at=T_C),
        AuditEvent(
            event_type="triage.no_action_discarded",
            conversation_id=thread_a.conversation_id,
            mailbox=SALES,
            payload={"has_action_items": False, "is_automated": True},
            actor="system",
        ),
        AuditEvent(
            event_type="triage.no_action_discarded",
            conversation_id=thread_b.conversation_id,
            mailbox=SALES,
            payload={"has_action_items": False, "is_automated": True},
            actor="system",
        ),
        AuditEvent(
            event_type="triage.no_action_discarded",
            conversation_id=thread_c.conversation_id,
            mailbox=SALES,
            payload={"has_action_items": False, "is_automated": True},
            actor="system",
        ),
    )
    await db_session.commit()

    await _escalate(db_session, thread_a, assessed="LOW", now=T0)
    await _escalate(db_session, thread_b, assessed="LOW", now=T_B)
    applied_c = await _escalate(db_session, thread_c, assessed="LOW", now=T_C)
    await db_session.commit()

    rows = [
        await thread_repo.get_by_id(db_session, thread_a.id, TenantScope.single(thread_a.mailbox)),
        await thread_repo.get_by_id(db_session, thread_b.id, TenantScope.single(thread_b.mailbox)),
        await thread_repo.get_by_id(db_session, thread_c.id, TenantScope.single(thread_c.mailbox)),
    ]
    assert applied_c == "LOW"
    assert [row.urgency for row in rows] == ["LOW", "LOW", "LOW"]
    assert all(row is not None and row.urgency_reason is None for row in rows)


@pytest.mark.asyncio
async def test_third_fingerprint_thread_within_48h_floors_all_critical(db_session) -> None:
    thread_a = _thread(conversation_id="alert-a", subject=DISK_90, last_message_at=T0)
    thread_b = _thread(conversation_id="alert-b", subject=DISK_90, last_message_at=T_B)
    thread_c = _thread(conversation_id="alert-c", subject=DISK_90, last_message_at=T_C)
    await _persist(
        db_session,
        thread_a,
        thread_b,
        thread_c,
        _message(thread_a, received_at=T0),
        _message(thread_b, received_at=T_B),
        _message(thread_c, received_at=T_C),
        _draft(thread_a, created_at=T0),
        _draft(thread_b, created_at=T_B),
        _draft(thread_c, created_at=T_C),
    )
    await db_session.commit()

    await _escalate(db_session, thread_a, assessed="NORMAL", now=T0)
    await _escalate(db_session, thread_b, assessed="NORMAL", now=T_B)
    applied_c = await _escalate(db_session, thread_c, assessed="NORMAL", now=T_C)
    await db_session.commit()

    rows = [
        await thread_repo.get_by_id(db_session, thread_a.id, TenantScope.single(thread_a.mailbox)),
        await thread_repo.get_by_id(db_session, thread_b.id, TenantScope.single(thread_b.mailbox)),
        await thread_repo.get_by_id(db_session, thread_c.id, TenantScope.single(thread_c.mailbox)),
    ]
    assert applied_c == "CRITICAL"
    assert [row.urgency for row in rows] == ["CRITICAL", "CRITICAL", "CRITICAL"]
    assert rows[2] is not None
    assert rows[2].urgency_reason == BUMP_3


@pytest.mark.asyncio
async def test_resolved_thread_drops_out_of_cluster_so_third_is_only_high(db_session) -> None:
    thread_a = _thread(
        conversation_id="alert-a",
        subject=DISK_90,
        last_message_at=T0,
        state=ThreadStateEnum.RESOLVED.value,
    )
    thread_b = _thread(conversation_id="alert-b", subject=DISK_90, last_message_at=T_B)
    thread_c = _thread(conversation_id="alert-c", subject=DISK_90, last_message_at=T_C)
    await _persist(
        db_session,
        thread_a,
        thread_b,
        thread_c,
        _message(thread_a, received_at=T0),
        _message(thread_b, received_at=T_B),
        _message(thread_c, received_at=T_C),
        _draft(thread_b, created_at=T_B),
        _draft(thread_c, created_at=T_C),
    )
    await db_session.commit()

    await _escalate(db_session, thread_b, assessed="NORMAL", now=T_B)
    applied_c = await _escalate(db_session, thread_c, assessed="NORMAL", now=T_C)
    await db_session.commit()

    a = await thread_repo.get_by_id(db_session, thread_a.id, TenantScope.single(thread_a.mailbox))
    b = await thread_repo.get_by_id(db_session, thread_b.id, TenantScope.single(thread_b.mailbox))
    c = await thread_repo.get_by_id(db_session, thread_c.id, TenantScope.single(thread_c.mailbox))
    assert applied_c == "HIGH"
    assert a is not None and a.urgency == "NORMAL"
    assert b is not None and b.urgency == "HIGH"
    assert c is not None and c.urgency == "HIGH"


@pytest.mark.asyncio
async def test_same_thread_three_inbounds_still_critical(db_session) -> None:
    thread = _thread(conversation_id="alert-solo", subject=DISK_90, last_message_at=T_C)
    await _persist(
        db_session,
        thread,
        _message(thread, received_at=T0),
        _message(thread, received_at=T_B),
        _message(thread, received_at=T_C),
        _draft(thread, created_at=T_C),
    )
    await db_session.commit()

    applied = await _escalate(db_session, thread, assessed="NORMAL", now=T_C)
    await db_session.commit()
    fresh = await thread_repo.get_by_id(db_session, thread.id, TenantScope.single(thread.mailbox))
    assert applied == "CRITICAL"
    assert fresh is not None
    assert fresh.urgency == "CRITICAL"


@pytest.mark.asyncio
async def test_member_max_critical_raises_cluster_even_when_count_floor_is_high(
    db_session,
) -> None:
    thread_a = _thread(conversation_id="alert-a", subject=DISK_90, last_message_at=T0)
    thread_b = _thread(
        conversation_id="alert-b",
        subject=DISK_90,
        last_message_at=T_B,
        urgency="CRITICAL",
    )
    await _persist(
        db_session,
        thread_a,
        thread_b,
        _message(thread_a, received_at=T0),
        _message(thread_b, received_at=T_B),
        _draft(thread_a, created_at=T0, urgency="NORMAL"),
        _draft(thread_b, created_at=T_B, urgency="CRITICAL"),
    )
    await db_session.commit()

    await _escalate(db_session, thread_a, assessed="NORMAL", now=T0)
    applied_b = await _escalate(db_session, thread_b, assessed="CRITICAL", now=T_B)
    await db_session.commit()

    a = await thread_repo.get_by_id(db_session, thread_a.id, TenantScope.single(thread_a.mailbox))
    b = await thread_repo.get_by_id(db_session, thread_b.id, TenantScope.single(thread_b.mailbox))
    assert applied_b == "CRITICAL"
    assert a is not None and a.urgency == "CRITICAL"
    assert b is not None and b.urgency == "CRITICAL"


@pytest.mark.asyncio
async def test_near_subject_disk_91_joins_open_cluster_and_floors_high(db_session) -> None:
    thread_a = _thread(conversation_id="disk-90", subject=DISK_90, last_message_at=T0)
    thread_b = _thread(conversation_id="disk-91", subject=DISK_91, last_message_at=T_B)
    await _persist(
        db_session,
        thread_a,
        thread_b,
        _message(thread_a, received_at=T0),
        _message(thread_b, received_at=T_B),
        _draft(thread_a, created_at=T0),
        _draft(thread_b, created_at=T_B),
    )
    await db_session.commit()

    await _escalate(db_session, thread_a, assessed="NORMAL", now=T0)
    applied_b = await _escalate(db_session, thread_b, assessed="NORMAL", now=T_B)
    await db_session.commit()

    a = await thread_repo.get_by_id(db_session, thread_a.id, TenantScope.single(thread_a.mailbox))
    b = await thread_repo.get_by_id(db_session, thread_b.id, TenantScope.single(thread_b.mailbox))
    assert applied_b == "HIGH"
    assert a is not None and a.urgency == "HIGH"
    assert b is not None and b.urgency == "HIGH"


@pytest.mark.asyncio
async def test_invoice_from_same_sender_does_not_join_disk_cluster(db_session) -> None:
    disk = _thread(conversation_id="disk-90", subject=DISK_90, last_message_at=T0)
    invoice = _thread(
        conversation_id="january-invoice",
        subject="January invoice",
        last_message_at=T_B,
    )
    invoice.alert_fingerprint = None
    invoice.alert_signature = None
    invoice.alert_sender_norm = None
    await _persist(
        db_session,
        disk,
        invoice,
        _message(disk, received_at=T0),
        _message(invoice, received_at=T_B, sender=ALERTS),
        _draft(disk, created_at=T0),
        _draft(invoice, created_at=T_B),
    )
    await db_session.commit()

    await _escalate(db_session, disk, assessed="NORMAL", now=T0)
    applied_inv = await _escalate(db_session, invoice, assessed="NORMAL", now=T_B)
    await db_session.commit()

    d = await thread_repo.get_by_id(db_session, disk.id, TenantScope.single(disk.mailbox))
    inv = await thread_repo.get_by_id(db_session, invoice.id, TenantScope.single(invoice.mailbox))
    assert applied_inv == "NORMAL"
    assert d is not None and d.urgency == "NORMAL"
    assert inv is not None and inv.urgency == "NORMAL"


@pytest.mark.asyncio
async def test_mark_as_wrong_reverts_this_thread_and_suppresses_later_floor(
    db_session,
) -> None:
    thread_b = _thread(conversation_id="alert-b", subject=DISK_90, last_message_at=T_B)
    thread_c = _thread(conversation_id="alert-c", subject=DISK_90, last_message_at=T_C)
    await _persist(
        db_session,
        thread_b,
        thread_c,
        _message(thread_b, received_at=T_B),
        _message(thread_c, received_at=T_C),
        _draft(thread_b, created_at=T_B),
        _draft(thread_c, created_at=T_C),
    )
    await db_session.commit()

    await _escalate(db_session, thread_b, assessed="NORMAL", now=T_B)
    await _escalate(db_session, thread_c, assessed="NORMAL", now=T_C)
    await related_thread_service.propose_alert_associations(
        db_session, thread_id=thread_c.id, now=T_C
    )
    await db_session.commit()

    reverted = await recurrence_service.apply_urgency_feedback(
        db_session,
        thread_id=thread_c.id,
        action="wrong_escalation",
        actor="elise@example.com",
    )
    await db_session.commit()

    c = await thread_repo.get_by_id(db_session, thread_c.id, TenantScope.single(thread_c.mailbox))
    b = await thread_repo.get_by_id(db_session, thread_b.id, TenantScope.single(thread_b.mailbox))
    assert reverted == "NORMAL"
    assert c is not None and c.urgency == "NORMAL"
    assert b is not None and b.urgency == "HIGH"

    reviews = (
        await db_session.execute(
            text(
                "SELECT count(*) FROM thread_association_reviews "
                "WHERE source_thread_id = :src AND related_thread_id = :rel "
                "AND status <> 'dismissed'"
            ),
            {"src": thread_c.id, "rel": thread_b.id},
        )
    ).scalar_one()
    assert reviews == 1

    thread_d = _thread(conversation_id="alert-d", subject=DISK_90, last_message_at=T_D)
    await _persist(
        db_session,
        thread_d,
        _message(thread_d, received_at=T_D),
        _draft(thread_d, created_at=T_D),
    )
    await db_session.commit()
    applied_d = await _escalate(db_session, thread_d, assessed="NORMAL", now=T_D)
    await db_session.commit()
    d = await thread_repo.get_by_id(db_session, thread_d.id, TenantScope.single(thread_d.mailbox))
    assert applied_d == "NORMAL"
    assert d is not None and d.urgency == "NORMAL"


@pytest.mark.asyncio
async def test_propose_alert_associations_at_ingest_without_search(db_session) -> None:
    first = _thread(conversation_id="disk-90", subject=DISK_90, last_message_at=T0)
    second = _thread(conversation_id="disk-91", subject=DISK_91, last_message_at=T_B)
    await _persist(
        db_session,
        first,
        second,
        _message(first, received_at=T0),
        _message(second, received_at=T_B),
    )
    await db_session.commit()

    await related_thread_service.propose_alert_associations(
        db_session, thread_id=second.id, now=T_B
    )
    await db_session.commit()

    items = await related_thread_service.list_stored_associations(db_session, second.id)
    assert [row.thread_id for row in items] == [first.id]
    assert "same_sender" in items[0].match_reasons
    assert "near_subject" in items[0].match_reasons


@pytest.mark.asyncio
async def test_different_host_does_not_join_disk_cluster(db_session) -> None:
    disk_db1 = _thread(conversation_id="disk-db1", subject=DISK_90, last_message_at=T0)
    disk_db2 = _thread(
        conversation_id="disk-db2",
        subject="ALERT: Disk 90% on db-2",
        last_message_at=T_B,
    )
    await _persist(
        db_session,
        disk_db1,
        disk_db2,
        _message(disk_db1, received_at=T0),
        _message(disk_db2, received_at=T_B),
        _draft(disk_db1, created_at=T0),
        _draft(disk_db2, created_at=T_B),
    )
    await db_session.commit()

    await _escalate(db_session, disk_db1, assessed="NORMAL", now=T0)
    applied = await _escalate(db_session, disk_db2, assessed="NORMAL", now=T_B)
    await db_session.commit()

    a = await thread_repo.get_by_id(db_session, disk_db1.id, TenantScope.single(disk_db1.mailbox))
    b = await thread_repo.get_by_id(db_session, disk_db2.id, TenantScope.single(disk_db2.mailbox))
    assert applied == "NORMAL"
    assert a is not None and a.urgency == "NORMAL"
    assert b is not None and b.urgency == "NORMAL"


@pytest.mark.asyncio
async def test_missing_signature_columns_still_cluster_metric_slack(db_session) -> None:
    """Pre-042 rows have fingerprint only; 90% and 91% on db-1 must still floor HIGH."""
    disk_90 = _thread(conversation_id="disk-90", subject=DISK_90, last_message_at=T0)
    disk_91 = _thread(conversation_id="disk-91", subject=DISK_91, last_message_at=T_B)
    disk_90.alert_signature = None
    disk_90.alert_sender_norm = None
    disk_91.alert_signature = None
    disk_91.alert_sender_norm = None
    await _persist(
        db_session,
        disk_90,
        disk_91,
        _message(disk_90, received_at=T0),
        _message(disk_91, received_at=T_B),
        _draft(disk_90, created_at=T0),
        _draft(disk_91, created_at=T_B),
    )
    await db_session.commit()

    await _escalate(db_session, disk_90, assessed="NORMAL", now=T0)
    applied = await _escalate(db_session, disk_91, assessed="NORMAL", now=T_B)
    await db_session.commit()

    a = await thread_repo.get_by_id(db_session, disk_90.id, TenantScope.single(disk_90.mailbox))
    b = await thread_repo.get_by_id(db_session, disk_91.id, TenantScope.single(disk_91.mailbox))
    assert applied == "HIGH"
    assert a is not None and a.urgency == "HIGH"
    assert b is not None and b.urgency == "HIGH"
    assert a.alert_signature == b.alert_signature
    assert a.alert_signature is not None


@pytest.mark.asyncio
async def test_two_inbounds_plus_sibling_is_three_alerts_critical(db_session) -> None:
    """Grain is cluster inbound count: 2 in A + 1 in B = 3 → CRITICAL."""
    thread_a = _thread(conversation_id="alert-a", subject=DISK_90, last_message_at=T_B)
    thread_b = _thread(conversation_id="alert-b", subject=DISK_90, last_message_at=T_C)
    await _persist(
        db_session,
        thread_a,
        thread_b,
        _message(thread_a, received_at=T0),
        _message(thread_a, received_at=T_B),
        _message(thread_b, received_at=T_C),
        _draft(thread_a, created_at=T_B),
        _draft(thread_b, created_at=T_C),
    )
    await db_session.commit()

    await _escalate(db_session, thread_a, assessed="NORMAL", now=T_B)
    applied_b = await _escalate(db_session, thread_b, assessed="NORMAL", now=T_C)
    await db_session.commit()

    a = await thread_repo.get_by_id(db_session, thread_a.id, TenantScope.single(thread_a.mailbox))
    b = await thread_repo.get_by_id(db_session, thread_b.id, TenantScope.single(thread_b.mailbox))
    assert applied_b == "CRITICAL"
    assert a is not None and a.urgency == "CRITICAL"
    assert b is not None and b.urgency == "CRITICAL"
    assert b.urgency_reason == BUMP_3


@pytest.mark.asyncio
async def test_second_escalate_does_not_reemit_audit(db_session) -> None:
    thread_a = _thread(conversation_id="alert-a", subject=DISK_90, last_message_at=T0)
    thread_b = _thread(conversation_id="alert-b", subject=DISK_90, last_message_at=T_B)
    await _persist(
        db_session,
        thread_a,
        thread_b,
        _message(thread_a, received_at=T0),
        _message(thread_b, received_at=T_B),
        _draft(thread_a, created_at=T0),
        _draft(thread_b, created_at=T_B),
    )
    await db_session.commit()

    await _escalate(db_session, thread_a, assessed="NORMAL", now=T0)
    await _escalate(db_session, thread_b, assessed="NORMAL", now=T_B)
    await db_session.commit()
    first = await audit_repo.list_raw_by_conversation(
        db_session, thread_b.conversation_id, mailbox=SALES
    )
    first_count = sum(
        1 for row in first if row["event_type"] == "thread.urgency.recurrence_escalated"
    )

    await _escalate(db_session, thread_b, assessed="NORMAL", now=T_B)
    await db_session.commit()
    second = await audit_repo.list_raw_by_conversation(
        db_session, thread_b.conversation_id, mailbox=SALES
    )
    second_count = sum(
        1 for row in second if row["event_type"] == "thread.urgency.recurrence_escalated"
    )
    assert first_count == 1
    assert second_count == 1


@pytest.mark.asyncio
async def test_mark_as_wrong_after_ratchet_reverts_to_pre_recurrence(
    db_session,
) -> None:
    thread_a = _thread(conversation_id="alert-a", subject=DISK_90, last_message_at=T0)
    thread_b = _thread(conversation_id="alert-b", subject=DISK_90, last_message_at=T_B)
    thread_c = _thread(conversation_id="alert-c", subject=DISK_90, last_message_at=T_C)
    await _persist(
        db_session,
        thread_a,
        thread_b,
        thread_c,
        _message(thread_a, received_at=T0),
        _message(thread_b, received_at=T_B),
        _message(thread_c, received_at=T_C),
        _draft(thread_a, created_at=T0),
        _draft(thread_b, created_at=T_B),
        _draft(thread_c, created_at=T_C),
    )
    await db_session.commit()

    await _escalate(db_session, thread_a, assessed="NORMAL", now=T0)
    await _escalate(db_session, thread_b, assessed="NORMAL", now=T_B)
    await _escalate(db_session, thread_c, assessed="NORMAL", now=T_C)
    await db_session.commit()
    before = await thread_repo.get_by_id(
        db_session, thread_b.id, TenantScope.single(thread_b.mailbox)
    )
    assert before is not None and before.urgency == "CRITICAL"

    reverted = await recurrence_service.apply_urgency_feedback(
        db_session,
        thread_id=thread_b.id,
        action="wrong_escalation",
        actor="elise@example.com",
    )
    await db_session.commit()
    b = await thread_repo.get_by_id(db_session, thread_b.id, TenantScope.single(thread_b.mailbox))
    assert reverted == "NORMAL"
    assert b is not None and b.urgency == "NORMAL"


@pytest.mark.asyncio
async def test_mark_as_wrong_without_escalation_is_rejected(db_session) -> None:
    thread = _thread(conversation_id="alert-a", subject=DISK_90, last_message_at=T0)
    await _persist(
        db_session,
        thread,
        _message(thread, received_at=T0),
        _draft(thread, created_at=T0),
    )
    await db_session.commit()

    with pytest.raises(ThreadStateError):
        await recurrence_service.apply_urgency_feedback(
            db_session,
            thread_id=thread.id,
            action="wrong_escalation",
            actor="elise@example.com",
        )
    fresh = await thread_repo.get_by_id(db_session, thread.id, TenantScope.single(thread.mailbox))
    assert fresh is not None and fresh.urgency == "NORMAL"


@pytest.mark.asyncio
async def test_recurrence_hint_names_similar_thread_count() -> None:
    hint = recurrence_service.recurrence_hint(1, similar_thread_count=2)
    assert hint is not None
    assert "2 similar automated alerts" in hint
    assert "HIGH" in hint
