"""Persist Graph uniqueBody on messages so review can show the new reply only."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.core.tenant_scope import TenantScope
from app.repositories import message_repo, thread_repo

pytestmark = pytest.mark.db

MAILBOX = "sales@example.com"


@pytest.mark.asyncio
async def test_create_message_persists_unique_body_text(db_session) -> None:
    thread = await thread_repo.upsert_thread(
        db_session,
        mailbox=MAILBOX,
        conversation_id="conv-unique-body-1",
        subject="Re: Vercel deploy",
        last_message_at=datetime(2026, 8, 22, 14, 43, tzinfo=UTC),
    )
    row = await message_repo.create_message(
        db_session,
        thread_id=thread.id,
        graph_message_id="AAMk-unique-1",
        direction="outbound",
        sender=MAILBOX,
        body_text=(
            "Will do.\n\n"
            "From: Alice <alice@example.com>\n"
            "Sent: Monday, August 22, 2022 10:43 AM\n"
            "To: sales@example.com\n"
            "Subject: Re: Vercel deploy\n"
        ),
        body_preview="Will do.",
        received_at=datetime(2026, 8, 22, 14, 43, tzinfo=UTC),
        unique_body_text="Will do.",
    )
    await db_session.commit()

    loaded = await message_repo.get_by_id(db_session, row.id, TenantScope.single(MAILBOX))
    assert loaded is not None
    assert loaded.unique_body_text == "Will do."
    assert "From: Alice" in loaded.body_text


@pytest.mark.asyncio
async def test_create_message_fills_empty_body_on_conflict(db_session) -> None:
    thread = await thread_repo.upsert_thread(
        db_session,
        mailbox=MAILBOX,
        conversation_id="conv-fill-empty-1",
        subject="Re: Vercel deploy",
        last_message_at=datetime(2026, 8, 22, 14, 43, tzinfo=UTC),
    )
    empty = await message_repo.create_message(
        db_session,
        thread_id=thread.id,
        graph_message_id="AAMk-fill-1",
        direction="outbound",
        sender=MAILBOX,
        body_text="  \n",
        body_preview=None,
        unique_body_text="",
        received_at=datetime(2026, 8, 22, 14, 43, tzinfo=UTC),
        body_clean="",
    )
    filled = await message_repo.create_message(
        db_session,
        thread_id=thread.id,
        graph_message_id="AAMk-fill-1",
        direction="outbound",
        sender=MAILBOX,
        body_text="Will do.",
        body_preview="Will do.",
        unique_body_text="Will do.",
        received_at=datetime(2026, 8, 22, 14, 43, tzinfo=UTC),
        body_content_type="text",
        body_clean="Will do.",
        body_clean_version=1,
        body_clean_computed_at=datetime(2026, 8, 22, 14, 44, tzinfo=UTC),
    )
    await db_session.commit()

    assert filled.id == empty.id
    loaded = await message_repo.get_by_id(db_session, empty.id, TenantScope.single(MAILBOX))
    assert loaded is not None
    assert loaded.body_text == "Will do."
    assert loaded.body_preview == "Will do."
    assert loaded.unique_body_text == "Will do."
    assert loaded.body_clean == "Will do."


@pytest.mark.asyncio
async def test_create_message_does_not_shrink_nonempty_body(db_session) -> None:
    thread = await thread_repo.upsert_thread(
        db_session,
        mailbox=MAILBOX,
        conversation_id="conv-no-shrink-1",
        subject="Re: Vercel deploy",
        last_message_at=datetime(2026, 8, 22, 14, 43, tzinfo=UTC),
    )
    first = await message_repo.create_message(
        db_session,
        thread_id=thread.id,
        graph_message_id="AAMk-noshink-1",
        direction="outbound",
        sender=MAILBOX,
        body_text="Will do.",
        body_preview="Will do.",
        unique_body_text="Will do.",
        received_at=datetime(2026, 8, 22, 14, 43, tzinfo=UTC),
    )
    second = await message_repo.create_message(
        db_session,
        thread_id=thread.id,
        graph_message_id="AAMk-noshink-1",
        direction="outbound",
        sender=MAILBOX,
        body_text="",
        body_preview=None,
        unique_body_text="",
        received_at=datetime(2026, 8, 22, 14, 50, tzinfo=UTC),
    )
    await db_session.commit()

    assert second.id == first.id
    loaded = await message_repo.get_by_id(db_session, first.id, TenantScope.single(MAILBOX))
    assert loaded is not None
    assert loaded.body_text == "Will do."
    assert loaded.unique_body_text == "Will do."
