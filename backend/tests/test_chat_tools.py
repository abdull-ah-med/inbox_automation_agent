"""Chat RAG tools: Anthropic search_result packing, tool names, get_thread ACL.

Oracles from Anthropic Search results docs: source, title, text content,
citations.enabled. Product source is the thread path, not a guessed URL.
get_thread must not leak a thread outside the UI mailbox filter.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import Settings
from app.llm.chat_tools import CHAT_TOOLS, search_results_from_hits
from app.models.schemas.search import SearchHit

THREAD_A = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
SALES = "sales@example.com"
CR = "cr@example.com"


def _hit() -> SearchHit:
    return SearchHit(
        thread_id=THREAD_A,
        mailbox=SALES,
        conversation_id="conv-a",
        subject="Invoice dispute — overdue billing",
        state="REQUIRES_HUMAN",
        urgency="HIGH",
        snippet="Please review the overdue billing packet.",
        score=0.02,
        last_message_at=datetime(2026, 8, 5, 15, 0, tzinfo=UTC),
    )


def test_search_results_use_thread_path_and_enable_citations() -> None:
    blocks = search_results_from_hits([_hit()])
    assert len(blocks) == 1
    block = blocks[0]
    assert block["type"] == "search_result"
    assert block["source"] == f"/threads/{THREAD_A}"
    assert block["title"] == "Invoice dispute — overdue billing"
    assert block["citations"] == {"enabled": True}
    assert block["content"][0]["type"] == "text"
    assert "overdue billing packet" in block["content"][0]["text"]
    assert str(THREAD_A) not in block["content"][0]["text"]
    texts = [item["text"] for item in block["content"]]
    assert any("overdue billing packet" in text for text in texts)
    assert any("2026-08-05T15:00:00+00:00" in text for text in texts)
    assert any("state: REQUIRES_HUMAN" in text for text in texts)
    assert any("urgency: HIGH" in text for text in texts)
    assert texts[0] != texts[1]
    assert block.get("cache_control") == {"type": "ephemeral"}


def test_chat_tools_are_the_three_read_only_inbox_tools() -> None:
    names = [tool["name"] for tool in CHAT_TOOLS]
    assert names == ["search_mail", "list_recent_threads", "get_thread"]
    assert "send_mail" not in names
    assert "approve_draft" not in names
    assert all(tool.get("strict") is True for tool in CHAT_TOOLS)
    assert CHAT_TOOLS[-1].get("cache_control") == {"type": "ephemeral"}
    assert "cache_control" not in CHAT_TOOLS[0]


def _settings() -> Settings:
    return Settings(
        environment="local",
        jwt_secret="c" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        target_mailboxes=f"{SALES},{CR}",
        anthropic_api_key="sk-ant-test",
        chat_model="claude-haiku-4-5",
        database_url=("postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test"),
        redis_url="redis://localhost:6379/15",
    )


def _thread(*, mailbox: str = SALES) -> SimpleNamespace:
    return SimpleNamespace(
        id=THREAD_A,
        mailbox=mailbox,
        conversation_id="conv-a",
        subject="Invoice dispute — overdue billing",
        state="REQUIRES_HUMAN",
        urgency="HIGH",
        last_message_at=datetime(2026, 8, 5, 15, 0, tzinfo=UTC),
    )


def _message(*, body: str = "Please review the overdue billing packet.") -> SimpleNamespace:
    return SimpleNamespace(
        body_text=body,
        body_preview=None,
        sender="client@example.com",
        received_at=datetime(2026, 8, 5, 15, 0, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_get_thread_hides_threads_outside_the_mailbox_filter() -> None:
    from app.services.chat_tools import execute_chat_tool

    with (
        patch(
            "app.services.chat_tools.thread_repo.get_by_id",
            AsyncMock(return_value=_thread(mailbox=SALES)),
        ),
        patch(
            "app.services.chat_tools.message_repo.list_by_thread",
            AsyncMock(side_effect=AssertionError("must not open a foreign mailbox")),
        ),
    ):
        result = await execute_chat_tool(
            AsyncMock(),
            _settings(),
            openai_client=None,
            name="get_thread",
            arguments={"thread_id": str(THREAD_A)},
            mailbox=CR,
            limit=10,
        )

    assert result.hits == []
    assert result.error == "Thread not found"
    assert result.status == "Opening thread"


@pytest.mark.asyncio
async def test_get_thread_returns_scrubbed_messages_for_an_allowed_thread() -> None:
    from app.services.chat_tools import execute_chat_tool

    with (
        patch(
            "app.services.chat_tools.thread_repo.get_by_id",
            AsyncMock(return_value=_thread()),
        ),
        patch(
            "app.services.chat_tools.message_repo.list_by_thread",
            AsyncMock(
                return_value=[_message(body="SSN 123-45-6789 is on the overdue billing packet.")]
            ),
        ),
    ):
        result = await execute_chat_tool(
            AsyncMock(),
            _settings(),
            openai_client=None,
            name="get_thread",
            arguments={"thread_id": str(THREAD_A)},
            mailbox=SALES,
            limit=10,
        )

    assert result.error is None
    assert result.status == "Opening thread"
    assert result.hits[0].thread_id == THREAD_A
    assert result.hits[0].mailbox == SALES
    assert "overdue billing packet" in result.hits[0].snippet
    assert "123-45-6789" not in result.hits[0].snippet
    assert "[REDACTED_SSN]" in result.hits[0].snippet
    assert str(THREAD_A) not in result.hits[0].snippet


@pytest.mark.asyncio
async def test_search_mail_empty_query_lists_recent_without_inventing_keywords() -> None:
    from app.models.schemas.search import SearchResponse
    from app.services.chat_tools import execute_chat_tool

    captured: dict = {}

    async def fake_search(*_args, **kwargs):
        captured.update(kwargs)
        return SearchResponse(query=kwargs["query"], mailbox=None, hits=[])

    with patch(
        "app.services.chat_tools.search_service.search_threads",
        fake_search,
    ):
        result = await execute_chat_tool(
            AsyncMock(),
            _settings(),
            openai_client=None,
            name="search_mail",
            arguments={"query": ""},
            mailbox=None,
            limit=10,
        )

    assert captured["query"] == ""
    assert captured["allow_empty"] is True
    assert result.hits == []
    assert result.status == "Searching mail"


def test_packed_search_result_includes_resolved_state() -> None:
    hit = _hit()
    resolved = hit.model_copy(update={"state": "RESOLVED", "subject": "Closed SampleClient file"})
    blocks = search_results_from_hits([resolved])
    blob = "\n".join(item["text"] for item in blocks[0]["content"])
    assert "RESOLVED" in blob
    assert "Closed SampleClient file" in blocks[0]["title"]


@pytest.mark.asyncio
async def test_search_mail_still_returns_resolved_threads() -> None:
    from app.models.schemas.search import SearchResponse
    from app.services.chat_tools import execute_chat_tool

    resolved = _hit().model_copy(
        update={"state": "RESOLVED", "subject": "Closed SampleClient file"},
    )

    async def fake_search(*_args, **kwargs):
        return SearchResponse(query=kwargs["query"], mailbox=None, hits=[resolved])

    with patch(
        "app.services.chat_tools.search_service.search_threads",
        fake_search,
    ):
        result = await execute_chat_tool(
            AsyncMock(),
            _settings(),
            openai_client=None,
            name="search_mail",
            arguments={"query": "Closed SampleClient file"},
            mailbox=None,
            limit=10,
        )

    assert [hit.thread_id for hit in result.hits] == [THREAD_A]
    assert result.hits[0].state == "RESOLVED"
    packed = search_results_from_hits(result.hits)
    blob = "\n".join(item["text"] for item in packed[0]["content"])
    assert "RESOLVED" in blob
    assert "Closed SampleClient file" in packed[0]["title"]


@pytest.mark.db
@pytest.mark.asyncio
async def test_list_recent_uses_needs_attention_and_keeps_low(db_session) -> None:
    from app.models.db.draft import Draft
    from app.models.db.thread import Thread
    from app.models.schemas.email import ThreadStateEnum
    from app.services.chat_tools import execute_chat_tool

    low = Thread(
        id=uuid.uuid4(),
        mailbox=SALES,
        conversation_id="chat-low",
        subject="Low but still open",
        state=ThreadStateEnum.DRAFTED.value,
        urgency="LOW",
        last_message_at=datetime(2026, 8, 18, 12, 0, tzinfo=UTC),
    )
    done = Thread(
        id=uuid.uuid4(),
        mailbox=SALES,
        conversation_id="chat-done",
        subject="No reply SampleClient",
        state=ThreadStateEnum.NO_ACTION.value,
        urgency="LOW",
        last_message_at=datetime(2026, 8, 18, 13, 0, tzinfo=UTC),
    )
    db_session.add_all([low, done])
    await db_session.flush()
    db_session.add(
        Draft(
            id=uuid.uuid4(),
            thread_id=low.id,
            message_id=str(uuid.uuid4()),
            subject=low.subject,
            body="draft",
            recipients={},
            teaching_note="note",
            created_at=datetime(2026, 8, 18, 12, 0, tzinfo=UTC),
            urgency="LOW",
        )
    )
    await db_session.commit()

    result = await execute_chat_tool(
        db_session,
        _settings(),
        openai_client=None,
        name="list_recent_threads",
        arguments={},
        mailbox=None,
        limit=20,
    )

    assert [hit.thread_id for hit in result.hits] == [low.id]
    assert result.hits[0].urgency == "LOW"
    packed = search_results_from_hits(result.hits)
    blob = "\n".join(item["text"] for item in packed[0]["content"])
    assert "NO_ACTION" not in blob
    assert "urgency: LOW" in blob
