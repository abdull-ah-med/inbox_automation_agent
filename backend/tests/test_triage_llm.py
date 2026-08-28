"""Unit tests for Haiku triage LLM caller (mocked Anthropic — never live)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import Settings
from app.core.exceptions import TriageError
from app.llm import triage as triage_llm
from app.llm.prompts import PROMPT_VERSION, TRIAGE_SYSTEM_PROMPT
from app.models.schemas.classification import TriageResultSchema
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema, ThreadContextSchema


def _email() -> EmailMessageSchema:
    return EmailMessageSchema(
        message_id="m1",
        conversation_id="c1",
        mailbox="elise@example.com",
        sender="vendor@example.com",
        subject="Need docs",
        body_text="Please send the intake packet.",
        body_preview="Please send",
        received_at=datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
        direction=EmailDirectionEnum.INBOUND,
        to_recipients=["elise@example.com"],
        cc_recipients=["ops@example.com"],
    )


def _context(email: EmailMessageSchema) -> ThreadContextSchema:
    return ThreadContextSchema(
        conversation_id=email.conversation_id,
        mailbox=email.mailbox,
        subject=email.subject,
        messages=[email],
    )


def _settings() -> Settings:
    return Settings(
        classification_model="claude-haiku-4-5",
        triage_max_tokens=200,
        anthropic_api_key="test-key",
    )


def _ok_triage() -> TriageResultSchema:
    return TriageResultSchema(
        is_spam=False,
        spam_reason=None,
        has_action_items=True,
        action_items_summary="Send intake packet",
        needs_context=False,
        context_reason=None,
    )


def test_build_user_content_includes_to_and_cc() -> None:
    email = _email()
    content = triage_llm._build_user_content(email, _context(email))
    assert "To: elise@example.com" in content
    assert "CC: ops@example.com" in content
    assert "Mailbox: elise@example.com" in content
    assert "Please send the intake packet." in content


def test_build_user_content_labels_junk_folder_as_outlook_location() -> None:
    email = _email().model_copy(update={"graph_folder": "junkemail"})
    content = triage_llm._build_user_content(email, _context(email))
    assert "Outlook location: Junk Email" in content


def test_build_user_content_omits_junk_label_for_inbox() -> None:
    email = _email().model_copy(update={"graph_folder": "inbox"})
    content = triage_llm._build_user_content(email, _context(email))
    assert "Outlook location: Junk Email" not in content


def test_build_user_content_includes_mailbox_owner_when_set() -> None:
    email = _email().model_copy(update={"mailbox": "sampleagent@sample-site.example.com"})
    content = triage_llm._build_user_content(
        email,
        _context(email),
        mailbox_owner="Elise",
    )
    assert "Mailbox: sampleagent@sample-site.example.com" in content
    assert "Mailbox owner: Elise (personal inbox — mail here is for Elise specifically)" in content


def test_build_user_content_omits_mailbox_owner_when_unset() -> None:
    content = triage_llm._build_user_content(_email(), _context(_email()))
    assert "Mailbox owner:" not in content


def test_build_user_content_treats_ingest_automated_as_hint_not_verdict() -> None:
    email = _email().model_copy(update={"is_automated": True})
    content = triage_llm._build_user_content(email, _context(email))
    assert "not a verdict" in content.lower()
    assert "Ingest flagged this message as automated (list/noreply headers)." not in content


@pytest.mark.asyncio
async def test_triage_email_happy_path_uses_settings_model_and_prompt() -> None:
    email = _email()
    client = AsyncMock()
    parsed = MagicMock()
    parsed.parsed_output = _ok_triage()
    parsed.usage = MagicMock(input_tokens=100, output_tokens=40)
    client.messages.parse = AsyncMock(return_value=parsed)

    result = await triage_llm.triage_email(
        client=client,
        settings=_settings(),
        email=email,
        thread_context=_context(email),
    )

    assert result.triage.has_action_items is True
    assert result.prompt_version == PROMPT_VERSION
    assert result.model == "claude-haiku-4-5"
    assert result.input_tokens == 100
    kwargs = client.messages.parse.await_args.kwargs
    assert kwargs["model"] == "claude-haiku-4-5"
    assert kwargs["max_tokens"] == 200
    assert kwargs["output_format"] is TriageResultSchema
    system = kwargs["system"]
    assert system[0]["text"] == TRIAGE_SYSTEM_PROMPT
    assert system[0]["cache_control"] == {"type": "ephemeral"}


@pytest.mark.asyncio
async def test_triage_email_retries_once_then_succeeds() -> None:
    email = _email()
    client = AsyncMock()
    ok = MagicMock()
    ok.parsed_output = _ok_triage()
    ok.usage = MagicMock(input_tokens=10, output_tokens=5)
    client.messages.parse = AsyncMock(
        side_effect=[
            TriageError("empty"),
            ok,
        ]
    )

    result = await triage_llm.triage_email(
        client=client,
        settings=_settings(),
        email=email,
        thread_context=_context(email),
    )
    assert result.triage.has_action_items is True
    assert client.messages.parse.await_count == 2


@pytest.mark.asyncio
async def test_triage_email_double_failure_raises_triage_error() -> None:
    email = _email()
    client = AsyncMock()
    client.messages.parse = AsyncMock(side_effect=ValueError("bad json"))

    with pytest.raises(TriageError, match="failed after retry"):
        await triage_llm.triage_email(
            client=client,
            settings=_settings(),
            email=email,
            thread_context=_context(email),
        )
    assert client.messages.parse.await_count == 2


@pytest.mark.asyncio
async def test_triage_email_missing_api_key_raises_clear_error() -> None:
    email = _email()
    client = AsyncMock()
    settings = Settings(
        classification_model="claude-haiku-4-5",
        triage_max_tokens=200,
        anthropic_api_key="",
    )

    with pytest.raises(TriageError, match="ANTHROPIC_API_KEY is not set"):
        await triage_llm.triage_email(
            client=client,
            settings=settings,
            email=email,
            thread_context=_context(email),
        )
    client.messages.parse.assert_not_awaited()
