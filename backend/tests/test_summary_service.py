"""Unit tests for message summary service (staleness + persist)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings
from app.core.exceptions import TriageError
from app.llm.email_clean import CLEAN_VERSION
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema
from app.models.schemas.summary import MessageSummarySchema
from app.repositories.message_repo import MessageSchema
from app.services import summary_service


def _email(**overrides: object) -> EmailMessageSchema:
    base: dict[str, object] = {
        "message_id": "graph-m1",
        "conversation_id": "c1",
        "mailbox": "elise@example.com",
        "sender": "vendor@example.com",
        "subject": "Need docs",
        "body_text": "Please send the intake packet by Friday.",
        "body_clean": "Please send the intake packet by Friday.",
        "body_preview": "Please send",
        "received_at": datetime(2026, 7, 22, tzinfo=UTC),
        "direction": EmailDirectionEnum.INBOUND,
        "to_recipients": ["elise@example.com"],
        "cc_recipients": [],
    }
    base.update(overrides)
    return EmailMessageSchema(**base)  # type: ignore[arg-type]


def _row(**overrides: object) -> MessageSchema:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "thread_id": uuid.uuid4(),
        "graph_message_id": "graph-m1",
        "direction": "inbound",
        "sender": "vendor@example.com",
        "body_text": "Please send the intake packet by Friday.",
        "body_preview": "Please send",
        "body_content_type": "text",
        "body_clean": "Please send the intake packet by Friday.",
        "body_clean_version": CLEAN_VERSION,
        "received_at": datetime(2026, 7, 22, tzinfo=UTC),
        "to_recipients": [],
        "cc_recipients": [],
        "summarized_at": None,
        "summary_clean_version": None,
        "summary_one_line": None,
        "summary_json": None,
    }
    values.update(overrides)
    return MessageSchema(**values)  # type: ignore[arg-type]


def test_summary_is_stale_when_never_summarized() -> None:
    assert summary_service.summary_is_stale(
        summarized_at=None,
        summary_clean_version=None,
        body_clean_version=CLEAN_VERSION,
    )


def test_summary_is_stale_when_clean_version_mismatch() -> None:
    assert summary_service.summary_is_stale(
        summarized_at=datetime.now(UTC),
        summary_clean_version=0,
        body_clean_version=CLEAN_VERSION,
    )


def test_summary_not_stale_when_versions_match() -> None:
    assert not summary_service.summary_is_stale(
        summarized_at=datetime.now(UTC),
        summary_clean_version=CLEAN_VERSION,
        body_clean_version=CLEAN_VERSION,
    )


@pytest.mark.asyncio
async def test_summarize_and_store_persists_clean_version() -> None:
    session = AsyncMock()
    email = _email()
    summary = MessageSummarySchema(
        intent="request",
        ask="Send intake packet",
        commitments=[],
        people=[],
        deadlines=["Friday"],
        open_questions=[],
        one_line="Vendor asks for intake packet by Friday.",
    )
    call_result = SimpleNamespace(
        summary=summary,
        model="claude-haiku-test",
        prompt_version="v1",
        input_tokens=10,
        output_tokens=20,
        latency_ms=5,
    )
    msg_pk = uuid.uuid4()

    with (
        patch(
            "app.services.summary_service.summary_llm.summarize_message",
            new=AsyncMock(return_value=call_result),
        ),
        patch(
            "app.services.summary_service.message_repo.update_summary",
            new=AsyncMock(),
        ) as update,
    ):
        ok = await summary_service.summarize_and_store(
            session,
            email=email,
            client=AsyncMock(),
            settings=Settings(environment="local"),
            message_pk=msg_pk,
            body_clean_version=CLEAN_VERSION,
        )

    assert ok is True
    update.assert_awaited_once()
    kwargs = update.await_args.kwargs
    assert kwargs["summary_clean_version"] == CLEAN_VERSION
    assert kwargs["summary_one_line"] == summary.one_line
    assert kwargs["summary_json"] == summary.model_dump()
    assert email.summary_one_line == summary.one_line


@pytest.mark.asyncio
async def test_summarize_current_if_needed_skips_fresh() -> None:
    session = AsyncMock()
    email = _email()
    row = _row(
        summarized_at=datetime.now(UTC),
        summary_clean_version=CLEAN_VERSION,
        summary_one_line="Already summarized.",
        summary_json={"one_line": "Already summarized."},
    )

    with (
        patch(
            "app.services.summary_service.message_repo.get_by_graph_id",
            new=AsyncMock(return_value=row),
        ),
        patch(
            "app.services.summary_service.summarize_and_store_safe",
            new=AsyncMock(),
        ) as store,
    ):
        await summary_service.summarize_current_if_needed(
            session,
            email=email,
            client=AsyncMock(),
            settings=Settings(environment="local"),
        )

    store.assert_not_awaited()
    assert email.summary_one_line == "Already summarized."


@pytest.mark.asyncio
async def test_summarize_and_store_safe_swallows_triage_error() -> None:
    with patch(
        "app.services.summary_service.summarize_and_store",
        new=AsyncMock(side_effect=TriageError("boom")),
    ):
        ok = await summary_service.summarize_and_store_safe(
            AsyncMock(),
            email=_email(),
            client=AsyncMock(),
            settings=Settings(environment="local"),
            message_pk=uuid.uuid4(),
            body_clean_version=CLEAN_VERSION,
        )
    assert ok is False


@pytest.mark.asyncio
async def test_summarize_and_store_skips_short_body() -> None:
    ok = await summary_service.summarize_and_store(
        AsyncMock(),
        email=_email(body_clean="x", body_text="x"),
        client=AsyncMock(),
        settings=Settings(environment="local"),
        message_pk=uuid.uuid4(),
    )
    assert ok is False


@pytest.mark.asyncio
async def test_backfill_sibling_summaries_runs_for_siblings() -> None:
    current = _email(message_id="current")
    sibling = _email(message_id="sibling")
    session = AsyncMock()
    begin_cm = AsyncMock()
    begin_cm.__aenter__ = AsyncMock(return_value=None)
    begin_cm.__aexit__ = AsyncMock(return_value=None)
    session.begin = MagicMock(return_value=begin_cm)
    factory_cm = AsyncMock()
    factory_cm.__aenter__ = AsyncMock(return_value=session)
    factory_cm.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=factory_cm)
    row = _row(graph_message_id="sibling")

    with (
        patch(
            "app.services.summary_service.message_repo.get_by_graph_id",
            new=AsyncMock(return_value=row),
        ),
        patch(
            "app.services.summary_service.summarize_and_store_safe",
            new=AsyncMock(return_value=True),
        ) as store,
    ):
        await summary_service.backfill_sibling_summaries(
            session_factory=factory,
            emails=[current, sibling],
            current_message_id="current",
            client=AsyncMock(),
            settings=Settings(environment="local"),
        )

    store.assert_awaited_once()


@pytest.mark.asyncio
async def test_backfill_sibling_summaries_noop_without_siblings() -> None:
    await summary_service.backfill_sibling_summaries(
        session_factory=MagicMock(),
        emails=[_email()],
        current_message_id="graph-m1",
        client=AsyncMock(),
        settings=Settings(environment="local"),
    )


@pytest.mark.asyncio
async def test_summarize_current_if_needed_stores_when_stale() -> None:
    session = AsyncMock()
    email = _email()
    row = _row(summarized_at=None, summary_clean_version=None)

    with (
        patch(
            "app.services.summary_service.message_repo.get_by_graph_id",
            new=AsyncMock(return_value=row),
        ),
        patch(
            "app.services.summary_service.summarize_and_store_safe",
            new=AsyncMock(return_value=True),
        ) as store,
    ):
        await summary_service.summarize_current_if_needed(
            session,
            email=email,
            client=AsyncMock(),
            settings=Settings(environment="local"),
        )

    store.assert_awaited_once()


def test_summary_is_stale_when_body_version_none_uses_clean_version() -> None:
    assert summary_service.summary_is_stale(
        summarized_at=datetime.now(UTC),
        summary_clean_version=0,
        body_clean_version=None,
    )


@pytest.mark.asyncio
async def test_summarize_and_store_resolves_message_pk() -> None:
    session = AsyncMock()
    email = _email()
    row = _row()
    summary = MessageSummarySchema(
        intent="request",
        ask="Send packet",
        commitments=[],
        people=[],
        deadlines=[],
        open_questions=[],
        one_line="Ask for packet.",
    )
    call_result = SimpleNamespace(
        summary=summary,
        model="claude-haiku-test",
        prompt_version="v1",
        input_tokens=1,
        output_tokens=1,
        latency_ms=1,
    )

    with (
        patch(
            "app.services.summary_service.message_repo.get_by_graph_id",
            new=AsyncMock(return_value=row),
        ),
        patch(
            "app.services.summary_service.summary_llm.summarize_message",
            new=AsyncMock(return_value=call_result),
        ),
        patch(
            "app.services.summary_service.message_repo.update_summary",
            new=AsyncMock(),
        ) as update,
    ):
        ok = await summary_service.summarize_and_store(
            session,
            email=email,
            client=AsyncMock(),
            settings=Settings(environment="local"),
        )

    assert ok is True
    assert update.await_args.kwargs["message_id"] == row.id
    assert update.await_args.kwargs["summary_clean_version"] == row.body_clean_version


@pytest.mark.asyncio
async def test_summarize_and_store_safe_swallows_unexpected_error() -> None:
    with patch(
        "app.services.summary_service.summarize_and_store",
        new=AsyncMock(side_effect=RuntimeError("db down")),
    ):
        ok = await summary_service.summarize_and_store_safe(
            AsyncMock(),
            email=_email(),
            client=AsyncMock(),
            settings=Settings(environment="local"),
            message_pk=uuid.uuid4(),
        )
    assert ok is False


@pytest.mark.asyncio
async def test_summarize_current_if_needed_noop_when_missing_row() -> None:
    with (
        patch(
            "app.services.summary_service.message_repo.get_by_graph_id",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.summary_service.summarize_and_store_safe",
            new=AsyncMock(),
        ) as store,
    ):
        await summary_service.summarize_current_if_needed(
            AsyncMock(),
            email=_email(),
            client=AsyncMock(),
            settings=Settings(environment="local"),
        )
    store.assert_not_awaited()


@pytest.mark.asyncio
async def test_backfill_skips_fresh_sibling() -> None:
    current = _email(message_id="current")
    sibling = _email(message_id="sibling")
    session = AsyncMock()
    begin_cm = AsyncMock()
    begin_cm.__aenter__ = AsyncMock(return_value=None)
    begin_cm.__aexit__ = AsyncMock(return_value=None)
    session.begin = MagicMock(return_value=begin_cm)
    factory_cm = AsyncMock()
    factory_cm.__aenter__ = AsyncMock(return_value=session)
    factory_cm.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=factory_cm)
    row = _row(
        graph_message_id="sibling",
        summarized_at=datetime.now(UTC),
        summary_clean_version=CLEAN_VERSION,
        summary_one_line="fresh",
        summary_json={"one_line": "fresh"},
    )

    with (
        patch(
            "app.services.summary_service.message_repo.get_by_graph_id",
            new=AsyncMock(return_value=row),
        ),
        patch(
            "app.services.summary_service.summarize_and_store_safe",
            new=AsyncMock(),
        ) as store,
    ):
        await summary_service.backfill_sibling_summaries(
            session_factory=factory,
            emails=[current, sibling],
            current_message_id="current",
            client=AsyncMock(),
            settings=Settings(environment="local"),
        )

    store.assert_not_awaited()
    assert sibling.summary_one_line == "fresh"
