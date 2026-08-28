"""Postgres oracles for related-thread finder and apply-treatment.

SampleClient 8/9 vs 8/14 is the worked sibling example. Search is doubled so this
file does not call OpenAI. Invoice from the same sender stays out.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import Settings
from app.core.exceptions import ThreadStateError
from app.core.tenant_scope import TenantScope
from app.models.db.draft import Draft
from app.models.db.message import Message
from app.models.db.thread import Thread
from app.models.schemas.email import ThreadStateEnum
from app.models.schemas.search import SearchHit, SearchResponse
from app.repositories import association_review_repo, thread_repo

pytestmark = pytest.mark.db

SALES = "sales@example.com"
SENDER = "rep@sample-client.example.com"
T_SRC = datetime(2026, 8, 9, 14, 0, tzinfo=UTC)
T_SIB = datetime(2026, 8, 14, 15, 0, tzinfo=UTC)
T_INV = datetime(2026, 8, 14, 16, 0, tzinfo=UTC)


def _settings() -> Settings:
    return Settings(
        environment="local",
        jwt_secret="c" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        target_mailboxes=SALES,
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
    )


def _thread(*, conversation_id: str, subject: str, last_message_at: datetime) -> Thread:
    return Thread(
        id=uuid.uuid4(),
        mailbox=SALES,
        conversation_id=conversation_id,
        subject=subject,
        state=ThreadStateEnum.DRAFTED.value,
        urgency="NORMAL",
        last_message_at=last_message_at,
    )


def _message(thread: Thread, *, received_at: datetime) -> Message:
    return Message(
        id=uuid.uuid4(),
        thread_id=thread.id,
        graph_message_id=str(uuid.uuid4()),
        direction="inbound",
        sender=SENDER,
        body_text="body",
        received_at=received_at,
        to_recipients=[],
        cc_recipients=[],
    )


def _draft(thread: Thread, *, created_at: datetime) -> Draft:
    return Draft(
        id=uuid.uuid4(),
        thread_id=thread.id,
        message_id=str(uuid.uuid4()),
        subject=thread.subject,
        body="draft",
        recipients={},
        teaching_note="note",
        created_at=created_at,
        urgency="NORMAL",
    )


def _hit(thread: Thread, *, cosine: float) -> SearchHit:
    return SearchHit(
        thread_id=thread.id,
        mailbox=thread.mailbox,
        conversation_id=thread.conversation_id,
        subject=thread.subject,
        state=thread.state,
        urgency=thread.urgency,
        snippet="preview",
        score=0.02,
        last_message_at=thread.last_message_at,
        similarity_score=cosine,
    )


async def _seed(session) -> dict[str, Thread]:
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
            _draft(source, created_at=T_SRC),
            _draft(sibling, created_at=T_SIB),
            _draft(invoice, created_at=T_INV),
        ]
    )
    await session.commit()
    return {"source": source, "sibling": sibling, "invoice": invoice}


@pytest.mark.asyncio
async def test_list_siblings_is_exactly_the_sampleclient_drip(db_session) -> None:
    rows = await _seed(db_session)
    source = rows["source"]
    sibling = rows["sibling"]
    invoice = rows["invoice"]
    fake = SearchResponse(
        query=f"from:{SENDER} SampleClient follow-up 8/9",
        mailbox=None,
        hits=[_hit(sibling, cosine=0.50), _hit(invoice, cosine=0.40)],
    )

    from app.services import related_thread_service

    with patch(
        "app.services.related_thread_service.search_service.search_threads",
        AsyncMock(return_value=fake),
    ):
        result = await related_thread_service.list_related(
            db_session,
            _settings(),
            source.id,
            purpose="siblings",
            openai_client=None,
        )

    assert [item.thread_id for item in result.items] == [sibling.id]


@pytest.mark.asyncio
async def test_apply_no_reply_sets_sibling_no_action_and_leaves_invoice(db_session) -> None:
    rows = await _seed(db_session)
    source = rows["source"]
    sibling = rows["sibling"]
    invoice = rows["invoice"]
    fake = SearchResponse(
        query="from:rep@sample-client.example.com SampleClient follow-up 8/9",
        mailbox=None,
        hits=[_hit(sibling, cosine=0.50), _hit(invoice, cosine=0.40)],
    )

    from app.services import related_thread_service

    with patch(
        "app.services.related_thread_service.search_service.search_threads",
        AsyncMock(return_value=fake),
    ):
        applied = await related_thread_service.apply_treatment(
            db_session,
            _settings(),
            source.id,
            treatment="no_reply",
            thread_ids=[sibling.id],
            reason="Same SampleClient drip",
            actor="elise@example.com",
            openai_client=None,
        )
    await db_session.commit()

    assert applied == [sibling.id]
    updated_sib = await thread_repo.get_by_id(
        db_session, sibling.id, TenantScope.single(sibling.mailbox)
    )
    updated_inv = await thread_repo.get_by_id(
        db_session, invoice.id, TenantScope.single(invoice.mailbox)
    )
    assert updated_sib is not None
    assert updated_sib.state == ThreadStateEnum.NO_ACTION.value
    assert updated_inv is not None
    assert updated_inv.state == ThreadStateEnum.DRAFTED.value


@pytest.mark.asyncio
async def test_skip_dismisses_so_siblings_are_not_proposed_again(db_session) -> None:
    rows = await _seed(db_session)
    source = rows["source"]
    sibling = rows["sibling"]
    invoice = rows["invoice"]
    fake = SearchResponse(
        query="from:rep@sample-client.example.com SampleClient follow-up 8/9",
        mailbox=None,
        hits=[_hit(sibling, cosine=0.50), _hit(invoice, cosine=0.40)],
    )

    from app.services import related_thread_service

    with patch(
        "app.services.related_thread_service.search_service.search_threads",
        AsyncMock(return_value=fake),
    ):
        first = await related_thread_service.list_related(
            db_session,
            _settings(),
            source.id,
            purpose="siblings",
            openai_client=None,
        )
        await related_thread_service.review_related(
            db_session,
            _settings(),
            source.id,
            sibling.id,
            status="dismissed",
            actor="elise@example.com",
        )
        await db_session.commit()
        second = await related_thread_service.list_related(
            db_session,
            _settings(),
            source.id,
            purpose="siblings",
            openai_client=None,
        )

    assert [item.thread_id for item in first.items] == [sibling.id]
    assert [item.thread_id for item in second.items] == []


@pytest.mark.asyncio
async def test_confirmed_association_is_packed_unconfirmed_is_not(db_session) -> None:
    rows = await _seed(db_session)
    source = rows["source"]
    sibling = rows["sibling"]
    invoice = rows["invoice"]

    from app.repositories import association_review_repo
    from app.services import related_thread_service

    await association_review_repo.upsert_proposed(
        db_session,
        source_thread_id=source.id,
        related_thread_id=sibling.id,
        score=0.9,
    )
    await related_thread_service.review_related(
        db_session,
        _settings(),
        source.id,
        sibling.id,
        status="confirmed",
        actor="elise@example.com",
    )
    await db_session.commit()

    packed = await related_thread_service.load_confirmed_contexts(db_session, source.id)
    assert [ctx.matched_conversation_id for ctx in packed] == [sibling.conversation_id]
    invoice_ids = {ctx.matched_conversation_id for ctx in packed}
    assert invoice.conversation_id not in invoice_ids


@pytest.mark.asyncio
async def test_list_related_keeps_near_subject_alert_from_search_hits(db_session) -> None:
    """Disk 91 is associated even when hybrid search only supplies cosine 0.50."""
    from app.services import related_thread_service
    from app.services.related_match import alert_fingerprint

    alerts = "alerts@ops.example"
    source = Thread(
        id=uuid.uuid4(),
        mailbox=SALES,
        conversation_id="disk-90",
        subject="ALERT: Disk 90% on db-1",
        state=ThreadStateEnum.DRAFTED.value,
        urgency="NORMAL",
        last_message_at=T_SRC,
        alert_fingerprint=alert_fingerprint(
            mailbox=SALES, sender=alerts, subject="ALERT: Disk 90% on db-1"
        ),
    )
    sibling = Thread(
        id=uuid.uuid4(),
        mailbox=SALES,
        conversation_id="disk-91",
        subject="ALERT: Disk 91% on db-1",
        state=ThreadStateEnum.DRAFTED.value,
        urgency="NORMAL",
        last_message_at=T_SIB,
        alert_fingerprint=alert_fingerprint(
            mailbox=SALES, sender=alerts, subject="ALERT: Disk 91% on db-1"
        ),
    )
    db_session.add_all([source, sibling])
    await db_session.flush()
    db_session.add_all(
        [
            Message(
                id=uuid.uuid4(),
                thread_id=source.id,
                graph_message_id=str(uuid.uuid4()),
                direction="inbound",
                sender=alerts,
                body_text="disk",
                received_at=T_SRC,
                to_recipients=[],
                cc_recipients=[],
                summary_json={"deadlines": []},
            ),
            Message(
                id=uuid.uuid4(),
                thread_id=sibling.id,
                graph_message_id=str(uuid.uuid4()),
                direction="inbound",
                sender=alerts,
                body_text="disk",
                received_at=T_SIB,
                to_recipients=[],
                cc_recipients=[],
                summary_json={"deadlines": []},
            ),
        ]
    )
    await db_session.commit()

    fake = SearchResponse(
        query=f"from:{alerts} ALERT: Disk 90% on db-1",
        mailbox=None,
        hits=[_hit(sibling, cosine=0.50)],
    )
    with patch(
        "app.services.related_thread_service.search_service.search_threads",
        AsyncMock(return_value=fake),
    ):
        result = await related_thread_service.list_related(
            db_session,
            _settings(),
            source.id,
            purpose="associated",
            openai_client=None,
        )
    assert [item.thread_id for item in result.items] == [sibling.id]
    assert "near_subject" in result.items[0].match_reasons


@pytest.mark.asyncio
async def test_confirm_without_proposal_is_rejected(db_session) -> None:
    rows = await _seed(db_session)
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
async def test_confirm_self_association_is_rejected(db_session) -> None:
    rows = await _seed(db_session)
    from app.services import related_thread_service

    with pytest.raises(ThreadStateError):
        await related_thread_service.review_related(
            db_session,
            _settings(),
            rows["source"].id,
            rows["source"].id,
            status="confirmed",
            actor="elise@example.com",
        )


@pytest.mark.asyncio
async def test_dismiss_hides_association_on_both_threads(db_session) -> None:
    rows = await _seed(db_session)
    source = rows["source"]
    sibling = rows["sibling"]
    from app.services import related_thread_service

    await association_review_repo.upsert_proposed(
        db_session,
        source_thread_id=source.id,
        related_thread_id=sibling.id,
        score=0.9,
    )
    await association_review_repo.upsert_proposed(
        db_session,
        source_thread_id=sibling.id,
        related_thread_id=source.id,
        score=0.9,
    )
    await related_thread_service.review_related(
        db_session,
        _settings(),
        source.id,
        sibling.id,
        status="dismissed",
        actor="elise@example.com",
    )
    await db_session.commit()

    from_source = await related_thread_service.list_stored_associations(db_session, source.id)
    from_sibling = await related_thread_service.list_stored_associations(db_session, sibling.id)
    assert from_source == []
    assert from_sibling == []


@pytest.mark.asyncio
async def test_load_confirmed_contexts_batches_thread_and_message_fetches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SPEC: no per-id N+1 — list_by_ids + list_by_thread_ids once each."""
    import uuid
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema
    from app.services import related_thread_service

    id_a, id_b = uuid.uuid4(), uuid.uuid4()
    pairs = [(id_a, 0.9), (id_b, 0.8)]
    thread_a = SimpleNamespace(
        id=id_a, conversation_id="conv-a", mailbox="sales@example.com", subject="A"
    )
    thread_b = SimpleNamespace(
        id=id_b, conversation_id="conv-b", mailbox="sales@example.com", subject="B"
    )
    msg_a = SimpleNamespace(id=uuid.uuid4())
    msg_b = SimpleNamespace(id=uuid.uuid4())

    list_by_ids = AsyncMock(return_value={id_a: thread_a, id_b: thread_b})
    list_by_thread_ids = AsyncMock(return_value={id_a: [msg_a], id_b: [msg_b]})
    get_by_id = AsyncMock(side_effect=AssertionError("must not N+1 get_by_id"))
    list_by_thread = AsyncMock(side_effect=AssertionError("must not N+1 list_by_thread"))

    def fake_row_to_email(row, **kwargs):  # type: ignore[no-untyped-def]
        return EmailMessageSchema(
            message_id=str(row.id),
            conversation_id=kwargs["conversation_id"],
            mailbox=kwargs["mailbox"],
            sender="sender@example.com",
            subject=kwargs["subject"],
            body_text="body",
            body_preview="body",
            received_at=datetime(2026, 8, 1, tzinfo=UTC),
            direction=EmailDirectionEnum.INBOUND,
        )

    monkeypatch.setattr(
        related_thread_service.association_review_repo,
        "confirmed_pairs",
        AsyncMock(return_value=pairs),
    )
    monkeypatch.setattr(related_thread_service.thread_repo, "list_by_ids", list_by_ids)
    monkeypatch.setattr(related_thread_service.message_repo, "list_by_thread_ids", list_by_thread_ids)
    monkeypatch.setattr(
        related_thread_service.thread_repo,
        "get_by_id_trusted",
        get_by_id,
        raising=False,
    )
    monkeypatch.setattr(
        related_thread_service.message_repo,
        "list_by_thread",
        list_by_thread,
        raising=False,
    )
    monkeypatch.setattr(related_thread_service, "_row_to_email", fake_row_to_email)

    packed = await related_thread_service.load_confirmed_contexts(AsyncMock(), uuid.uuid4())
    assert [c.matched_conversation_id for c in packed] == ["conv-a", "conv-b"]
    list_by_ids.assert_awaited_once()
    list_by_thread_ids.assert_awaited_once()
    get_by_id.assert_not_awaited()
    list_by_thread.assert_not_awaited()
