"""Repo-seam tenant guards. Insert in mailbox A, query with B, get None."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.core.tenant_scope import TenantScope
from app.models.db.message import Message
from app.models.db.thread import Thread
from app.repositories import message_repo, thread_repo

pytestmark = pytest.mark.db

MAILBOX_A = "sales@example.com"
MAILBOX_B = "cr@example.com"


@pytest.mark.asyncio
async def test_get_by_id_returns_none_for_wrong_mailbox(db_session) -> None:
    thread = Thread(
        id=uuid.uuid4(),
        mailbox=MAILBOX_A,
        conversation_id="tenant-scope-a",
        subject="Invoice dispute",
        state="NEW",
        last_message_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
    )
    db_session.add(thread)
    await db_session.commit()

    found = await thread_repo.get_by_id(
        db_session,
        thread.id,
        TenantScope.single(MAILBOX_A),
    )
    leaked = await thread_repo.get_by_id(
        db_session,
        thread.id,
        TenantScope.single(MAILBOX_B),
    )

    assert found is not None
    assert found.mailbox == MAILBOX_A
    assert leaked is None


@pytest.mark.asyncio
async def test_message_get_by_id_returns_none_for_wrong_mailbox(db_session) -> None:
    thread = Thread(
        id=uuid.uuid4(),
        mailbox=MAILBOX_A,
        conversation_id="tenant-scope-msg",
        subject="Packet",
        state="NEW",
        last_message_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
    )
    db_session.add(thread)
    await db_session.flush()
    message = Message(
        id=uuid.uuid4(),
        thread_id=thread.id,
        graph_message_id=str(uuid.uuid4()),
        direction="inbound",
        sender="vendor@example.com",
        body_text="Please review",
        received_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
        to_recipients=[],
        cc_recipients=[],
    )
    db_session.add(message)
    await db_session.commit()

    found = await message_repo.get_by_id(
        db_session,
        message.id,
        TenantScope.single(MAILBOX_A),
    )
    leaked = await message_repo.get_by_id(
        db_session,
        message.id,
        TenantScope.single(MAILBOX_B),
    )

    assert found is not None
    assert found.id == message.id
    assert leaked is None


def test_from_settings_empty_allowlist_denies_all() -> None:
    from app.core.config import Settings

    settings = Settings(
        environment="local",
        target_mailboxes="",
        jwt_secret="c" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
        _env_file=None,
    )
    scope = TenantScope.from_settings(settings)
    assert scope.mailboxes == ()
    assert scope.allows("sales@example.com") is False


@pytest.mark.asyncio
async def test_get_by_id_empty_scope_returns_none(db_session) -> None:
    thread = Thread(
        id=uuid.uuid4(),
        mailbox=MAILBOX_A,
        conversation_id="tenant-scope-empty",
        subject="Invoice",
        state="NEW",
        last_message_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
    )
    db_session.add(thread)
    await db_session.commit()

    found = await thread_repo.get_by_id(db_session, thread.id, TenantScope(mailboxes=()))
    assert found is None
