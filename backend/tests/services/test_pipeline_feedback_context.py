"""Ingest thread-fact extract must not inherit the draft packing size gate.

Oracle: a one-message thread still persists the Haiku fact literal. Draft packing
may send full bodies when len(messages) <= 5; that is not an extract skip.
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.models.db.message import Message
from app.models.db.thread import Thread
from app.repositories import thread_context_repo

pytestmark = pytest.mark.db

MAILBOX = "sales@example.com"
INBOUND_BODY = "Please cancel check 11111 for driver Ames"
FACT_INBOUND = "Sender asked to cancel check 11111 for driver Ames"


def _settings() -> Settings:
    return Settings(
        environment="local",
        jwt_secret="c" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        target_mailboxes=MAILBOX,
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
        anthropic_api_key="sk-test",
        thread_context_enabled=True,
        thread_full_if_at_most=5,
    )


def _haiku_response(facts: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=json.dumps({"facts": facts}))]
    )


async def _seed_one_message_thread(session) -> tuple[Thread, Message]:
    thread = Thread(
        id=uuid.uuid4(),
        mailbox=MAILBOX,
        conversation_id=str(uuid.uuid4()),
        subject="Cancel check 11111",
        state="NEW",
    )
    session.add(thread)
    await session.flush()
    inbound = Message(
        id=uuid.uuid4(),
        thread_id=thread.id,
        graph_message_id=str(uuid.uuid4()),
        direction="inbound",
        sender="buyer@acme.com",
        body_text=INBOUND_BODY,
        received_at=datetime(2026, 9, 4, 12, 0, tzinfo=UTC),
        to_recipients=[MAILBOX],
    )
    session.add(inbound)
    await session.commit()
    return thread, inbound


@pytest.mark.asyncio
async def test_ingest_extracts_facts_for_a_one_message_thread(db_session) -> None:
    from app.services.pipeline.feedback_context import extract_thread_context_safe

    thread, inbound = await _seed_one_message_thread(db_session)
    client = AsyncMock()
    client.messages.create = AsyncMock(
        return_value=_haiku_response([{"text": FACT_INBOUND, "source_message_id": str(inbound.id)}])
    )

    @asynccontextmanager
    async def session_factory():
        yield db_session

    await extract_thread_context_safe(
        thread_id=thread.id,
        client=client,
        settings=_settings(),
        session_factory=session_factory,
        source="pipeline",
    )

    active = await thread_context_repo.list_active_facts(db_session, thread.id)
    assert [row.body for row in active] == [FACT_INBOUND]
    pointer = await thread_context_repo.get(db_session, thread.id)
    assert pointer is not None
    assert pointer.extract_input_hash != ""
