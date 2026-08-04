"""Unit tests for Haiku message summary LLM caller (mocked — never live)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import Settings
from app.core.exceptions import TriageError
from app.llm import message_summary as summary_llm
from app.llm.prompts import MESSAGE_SUMMARY_SYSTEM_PROMPT, PROMPT_VERSION
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema
from app.models.schemas.summary import MessageSummarySchema


def _email(**overrides: object) -> EmailMessageSchema:
    base: dict[str, object] = {
        "message_id": "m1",
        "conversation_id": "c1",
        "mailbox": "elise@example.com",
        "sender": "vendor@example.com",
        "subject": "Need docs",
        "body_text": "Please send the intake packet by Friday morning.",
        "body_clean": "Please send the intake packet by Friday morning.",
        "body_preview": "Please send",
        "received_at": datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
        "direction": EmailDirectionEnum.INBOUND,
        "to_recipients": ["elise@example.com"],
        "cc_recipients": [],
    }
    base.update(overrides)
    return EmailMessageSchema(**base)  # type: ignore[arg-type]


def _settings(**overrides: object) -> Settings:
    values = {
        "classification_model": "claude-haiku-4-5",
        "anthropic_api_key": "test-key",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _ok_summary() -> MessageSummarySchema:
    return MessageSummarySchema(
        intent="request",
        ask="Send intake packet",
        commitments=[],
        people=[],
        deadlines=["Friday morning"],
        open_questions=[],
        one_line="Vendor asks for intake packet by Friday morning.",
    )


def test_should_summarize_rejects_short_body() -> None:
    assert not summary_llm.should_summarize_body(_email(body_clean="hi", body_text="hi"))


def test_should_summarize_accepts_long_enough_body() -> None:
    assert summary_llm.should_summarize_body(_email())


def test_build_user_content_uses_clean_body() -> None:
    email = _email(
        body_text="RAW with -----Original Message----- junk",
        body_clean="Please send the intake packet by Friday morning.",
    )
    content = summary_llm._build_user_content(email)
    assert "Please send the intake packet by Friday morning." in content
    assert "Original Message" not in content
    assert "Subject: Need docs" in content


@pytest.mark.asyncio
async def test_summarize_message_happy_path() -> None:
    client = AsyncMock()
    parsed = MagicMock()
    parsed.parsed_output = _ok_summary()
    parsed.usage = MagicMock(input_tokens=40, output_tokens=20)
    client.messages.parse = AsyncMock(return_value=parsed)

    result = await summary_llm.summarize_message(
        client=client,
        settings=_settings(),
        email=_email(),
    )

    assert result.summary.one_line.startswith("Vendor asks")
    assert result.prompt_version == PROMPT_VERSION
    assert result.model == "claude-haiku-4-5"
    assert result.input_tokens == 40
    kwargs = client.messages.parse.await_args.kwargs
    assert kwargs["max_tokens"] == summary_llm.SUMMARY_MAX_TOKENS
    assert kwargs["output_format"] is MessageSummarySchema
    assert kwargs["system"][0]["text"] == MESSAGE_SUMMARY_SYSTEM_PROMPT
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}


@pytest.mark.asyncio
async def test_summarize_message_requires_api_key() -> None:
    with pytest.raises(TriageError, match="ANTHROPIC_API_KEY"):
        await summary_llm.summarize_message(
            client=AsyncMock(),
            settings=_settings(anthropic_api_key=""),
            email=_email(),
        )


@pytest.mark.asyncio
async def test_summarize_message_retries_then_succeeds() -> None:
    client = AsyncMock()
    ok = MagicMock()
    ok.parsed_output = _ok_summary()
    ok.usage = MagicMock(input_tokens=1, output_tokens=1)
    client.messages.parse = AsyncMock(
        side_effect=[TriageError("empty"), ok],
    )

    result = await summary_llm.summarize_message(
        client=client,
        settings=_settings(),
        email=_email(),
    )
    assert result.summary.intent == "request"
    assert client.messages.parse.await_count == 2


@pytest.mark.asyncio
async def test_summarize_message_fails_after_retry() -> None:
    client = AsyncMock()
    empty = MagicMock()
    empty.parsed_output = None
    empty.usage = None
    client.messages.parse = AsyncMock(return_value=empty)

    with pytest.raises(TriageError, match="failed after retry"):
        await summary_llm.summarize_message(
            client=client,
            settings=_settings(),
            email=_email(),
        )
