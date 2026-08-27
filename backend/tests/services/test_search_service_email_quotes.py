"""Invoice 42 must outrank quoted 'invoice 1' history after quote stripping."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.core.config import Settings
from app.models.db.email_embedding import EmailEmbedding
from app.models.db.message import Message
from app.models.db.thread import Thread
from app.models.schemas.email import EmailMessageSchema
from app.services import embedding_service, search_service
from app.services.search_service import SEARCH_MODE_KEYWORD
from app.utils.email_quotes import strip_quoted_reply

SALES = "sales@example.com"
THREAD_A = uuid.UUID("42424242-4242-4242-4242-424242424242")
THREAD_B = uuid.UUID("11111111-1111-1111-1111-111111111111")


def _settings() -> Settings:
    return Settings(
        environment="local",
        jwt_secret="c" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        target_mailboxes=SALES,
        embedding_model="text-embedding-3-small",
        embedding_dimension=1536,
        embedding_min_similarity=0.78,
        embedding_candidate_k=15,
        rrf_k=60,
        openai_api_key="test-key",
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
        _env_file=None,
    )


def test_invoice_42_reply_beats_quoted_invoice_1_history() -> None:
    quoted = "\n".join(f"> invoice 1 line {i}" for i in range(400))
    newest = (
        f"New reply about invoice 42\n\nOn Thu, Aug 14, 2026 at 3:00 PM Alice wrote:\n{quoted}\n"
    )
    standalone = "Please pay invoice 1 by Friday."
    stripped_new = strip_quoted_reply(newest).text
    stripped_old = strip_quoted_reply(standalone).text
    assert stripped_new == "New reply about invoice 42"
    assert "invoice 1" not in stripped_new
    assert stripped_old == standalone
    query = "invoice 42"
    assert query in stripped_new
    assert query not in stripped_old


@pytest.mark.db
@pytest.mark.asyncio
async def test_search_ranks_invoice_42_thread_first_after_quote_strip(db_session) -> None:
    now = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
    quoted = "\n".join(f"> invoice 1 line {i}" for i in range(400))
    newest = (
        f"New reply about invoice 42\n\nOn Thu, Aug 14, 2026 at 3:00 PM Alice wrote:\n{quoted}\n"
    )
    standalone = "Please pay invoice 1 by Friday."
    db_session.add_all(
        [
            Thread(
                id=THREAD_A,
                mailbox=SALES,
                conversation_id="conv-invoice-42",
                subject="Re: packet",
                state="REQUIRES_HUMAN",
                urgency="HIGH",
                last_message_at=now,
            ),
            Thread(
                id=THREAD_B,
                mailbox=SALES,
                conversation_id="conv-invoice-1",
                subject="Invoice 1",
                state="DRAFTED",
                urgency="NORMAL",
                last_message_at=now,
            ),
        ]
    )
    await db_session.flush()
    msg_a = Message(
        id=uuid.uuid4(),
        thread_id=THREAD_A,
        graph_message_id=str(uuid.uuid4()),
        direction="inbound",
        sender="vendor@example.com",
        body_text=newest,
        body_preview="New reply about invoice 42",
        received_at=now,
        to_recipients=[SALES],
        cc_recipients=[],
    )
    msg_b = Message(
        id=uuid.uuid4(),
        thread_id=THREAD_B,
        graph_message_id=str(uuid.uuid4()),
        direction="inbound",
        sender="billing@example.com",
        body_text=standalone,
        body_preview=standalone,
        received_at=now,
        to_recipients=[SALES],
        cc_recipients=[],
    )
    db_session.add_all([msg_a, msg_b])
    await db_session.flush()
    doc_a = embedding_service.build_search_document(
        EmailMessageSchema(
            message_id=str(msg_a.id),
            conversation_id="conv-invoice-42",
            mailbox=SALES,
            sender="vendor@example.com",
            subject="Re: packet",
            body_text=newest,
            received_at=now,
            to_recipients=[SALES],
        )
    )
    doc_b = embedding_service.build_search_document(
        EmailMessageSchema(
            message_id=str(msg_b.id),
            conversation_id="conv-invoice-1",
            mailbox=SALES,
            sender="billing@example.com",
            subject="Invoice 1",
            body_text=standalone,
            received_at=now,
            to_recipients=[SALES],
        )
    )
    assert "invoice 42" in doc_a
    assert "invoice 1" not in doc_a
    zeros = [0.0] * 1536
    db_session.add_all(
        [
            EmailEmbedding(
                mailbox=SALES,
                conversation_id="conv-invoice-42",
                sender_email="vendor@example.com",
                recipient_emails=[SALES],
                cc_emails=[],
                embedding=zeros,
                sent_at=now,
                body_preview="New reply about invoice 42",
                search_document=doc_a,
                message_id=msg_a.id,
            ),
            EmailEmbedding(
                mailbox=SALES,
                conversation_id="conv-invoice-1",
                sender_email="billing@example.com",
                recipient_emails=[SALES],
                cc_emails=[],
                embedding=zeros,
                sent_at=now,
                body_preview=standalone,
                search_document=doc_b,
                message_id=msg_b.id,
            ),
        ]
    )
    await db_session.commit()

    result = await search_service.search_threads(
        db_session,
        _settings(),
        openai_client=None,
        query="invoice 42",
        mailbox=SALES,
        mode=SEARCH_MODE_KEYWORD,
    )
    assert result.hits, "expected at least one hit for invoice 42"
    assert result.hits[0].thread_id == THREAD_A
