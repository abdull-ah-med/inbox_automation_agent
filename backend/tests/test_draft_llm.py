"""Unit tests for Sonnet draft LLM caller (mocked Anthropic — never live)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import Settings
from app.core.exceptions import DraftGenerationError
from app.llm import draft_generator as draft_llm
from app.llm.prompts import BRIEFING_SYSTEM_PROMPT, DRAFT_SYSTEM_PROMPT, PROMPT_VERSION
from app.models.schemas.classification import TriageResultSchema
from app.models.schemas.draft import BriefingSchema, DraftSchema
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema, ThreadContextSchema
from app.models.schemas.email_triage_state import CrossThreadContextSchema


def _email() -> EmailMessageSchema:
    return EmailMessageSchema(
        message_id="m1",
        conversation_id="c1",
        mailbox="elise@example.com",
        sender="vendor@example.com",
        subject="Need docs",
        body_text="Please send the intake packet. SSN 123-45-6789.",
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


def _triage() -> TriageResultSchema:
    return TriageResultSchema(
        is_spam=False,
        spam_reason=None,
        has_action_items=True,
        action_items_summary="Send intake packet",
        needs_context=False,
        context_reason=None,
    )


def _settings() -> Settings:
    return Settings(
        draft_model="claude-sonnet-4-6",
        anthropic_api_key="test-key",
    )


def _ok_draft() -> DraftSchema:
    return DraftSchema(
        subject_line="Re: Need docs",
        reply_body="Happy to send the intake packet today.",
        suggested_recipients=[],
        forward_to=None,
        teaching_note="Vendor is asking for the intake packet; reply with the docs.",
        urgency="NORMAL",
        urgency_reason="Routine document request with no hard deadline.",
    )


def test_build_user_content_injects_reply_addressee_from_thread_tip() -> None:
    mailbox = "inquiries@sample-site.example.com"
    first = _email().model_copy(
        update={
            "message_id": "1",
            "mailbox": mailbox,
            "sender": "Smit Patel <smit.patel@sample-transport.example.com>",
            "to_recipients": [mailbox],
            "cc_recipients": [],
        }
    )
    tip = _email().model_copy(
        update={
            "message_id": "2",
            "mailbox": mailbox,
            "sender": mailbox,
            "direction": EmailDirectionEnum.OUTBOUND,
            "to_recipients": ["Dev@sample-site.example.com"],
            "cc_recipients": [],
            "body_text": "Following up with Dev.",
        }
    )
    context = ThreadContextSchema(
        conversation_id=first.conversation_id,
        mailbox=mailbox,
        subject=first.subject,
        messages=[first, tip],
    )
    content = draft_llm._build_user_content(tip, context, _triage())
    assert "Reply addressee (hard constraint):" in content
    assert "Salute: team" in content
    assert "Primary To: dev@sample-site.example.com" in content
    assert "Source: last_outbound_to" in content
    assert "Do not greet the thread opener" in content


def test_build_user_content_includes_owner_signoff_when_set() -> None:
    email = _email().model_copy(update={"mailbox": "sampleagent@sample-site.example.com"})
    content = draft_llm._build_user_content(
        email,
        _context(email),
        _triage(),
        mailbox_owner="Elise",
    )
    assert "Sign the reply as Elise" in content
    assert 'Closing name must be exactly "Elise"' in content


def test_build_user_content_omits_owner_signoff_when_unset() -> None:
    content = draft_llm._build_user_content(_email(), _context(_email()), _triage())
    assert "Sign the reply as" not in content


def test_build_user_content_includes_triage_and_optional_blocks() -> None:
    email = _email()
    prior = EmailMessageSchema(
        message_id="prior-1",
        conversation_id="conv-matched",
        mailbox=email.mailbox,
        sender="vendor@example.com",
        subject="Onboarding kickoff",
        body_text="Related prior thread about onboarding.",
        body_preview="Related prior thread about onboarding.",
        received_at=datetime(2026, 7, 1, tzinfo=UTC),
        direction=EmailDirectionEnum.INBOUND,
    )
    cross = CrossThreadContextSchema(
        matched_conversation_id="conv-matched",
        similarity_score=0.9,
        thread_messages=[prior],
    )
    content = draft_llm._build_user_content(
        email,
        _context(email),
        _triage(),
        cross_thread_context=cross,
        tone_references=["Thanks — sending the packet now."],
    )
    assert "To: elise@example.com" in content
    assert "has_action_items: True" in content
    assert "Send intake packet" in content
    assert "Related prior conversation" in content
    assert "conv-matched" in content
    assert "Related prior thread about onboarding." in content
    assert "Thanks — sending the packet now." in content


def test_build_user_content_packs_confirmed_associations_not_unconfirmed() -> None:
    email = _email()
    confirmed = EmailMessageSchema(
        message_id="assoc-1",
        conversation_id="sampleclient-8-14",
        mailbox="cr@example.com",
        sender="rep@sample-client.example.com",
        subject="SampleClient follow-up 8/14",
        body_text="SampleClient packet due Friday the 14th.",
        received_at=datetime(2026, 8, 14, tzinfo=UTC),
        direction=EmailDirectionEnum.INBOUND,
    )
    content = draft_llm._build_user_content(
        email,
        _context(email),
        _triage(),
        confirmed_associations=[
            CrossThreadContextSchema(
                matched_conversation_id="sampleclient-8-14",
                similarity_score=1.0,
                thread_messages=[confirmed],
            )
        ],
    )
    assert "SampleClient packet due Friday the 14th." in content
    assert "consider emailing cr@example.com" in content
    assert "January invoice unpaid" not in content


def test_build_user_content_includes_urgency_hints() -> None:
    content = draft_llm._build_user_content(
        _email(),
        _context(_email()),
        _triage(),
        urgency_hints=["[HIGH] Client has an SLA deadline tomorrow"],
    )
    assert "Past urgency corrections" in content
    assert "[HIGH] Client has an SLA deadline tomorrow" in content


def test_build_user_content_uses_cleaned_body_not_quotes() -> None:
    email = EmailMessageSchema(
        message_id="m1",
        conversation_id="c1",
        mailbox="elise@example.com",
        sender="vendor@example.com",
        subject="Need docs",
        body_text=(
            "Please send the packet.\n\n"
            "-----Original Message-----\nFrom: old\nQuoted junk\n"
            "CONFIDENTIALITY NOTICE: This email is confidential."
        ),
        body_clean="Please send the packet.",
        body_preview="Please send",
        received_at=datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
        direction=EmailDirectionEnum.INBOUND,
        to_recipients=["elise@example.com"],
        cc_recipients=[],
    )
    content = draft_llm._build_user_content(
        email,
        _context(email),
        _triage(),
    )
    assert "Please send the packet." in content
    assert "Original Message" not in content
    assert "CONFIDENTIALITY NOTICE" not in content
    assert "Quoted junk" not in content


def test_draft_schema_has_no_confidence_field() -> None:
    assert "confidence" not in DraftSchema.model_fields
    draft = _ok_draft()
    assert not hasattr(draft, "confidence") or "confidence" not in draft.model_dump()


@pytest.mark.asyncio
async def test_generate_draft_happy_path_uses_settings_model_and_prompt() -> None:
    email = _email()
    client = AsyncMock()
    parsed = MagicMock()
    parsed.parsed_output = _ok_draft()
    parsed.usage = MagicMock(input_tokens=200, output_tokens=80)
    client.messages.parse = AsyncMock(return_value=parsed)

    result = await draft_llm.generate_draft(
        email,
        _context(email),
        _triage(),
        client=client,
        settings=_settings(),
    )

    assert result.draft.urgency == "NORMAL"
    assert result.draft.teaching_note
    assert result.draft.urgency_reason
    assert "confidence" not in DraftSchema.model_fields
    assert result.prompt_version == PROMPT_VERSION
    assert result.model == "claude-sonnet-4-6"
    assert result.input_tokens == 200
    kwargs = client.messages.parse.await_args.kwargs
    assert kwargs["model"] == "claude-sonnet-4-6"
    assert kwargs["max_tokens"] == draft_llm.DRAFT_MAX_TOKENS
    assert kwargs["max_tokens"] <= 2048
    assert kwargs["output_format"] is DraftSchema
    system = kwargs["system"]
    assert system[0]["text"] == DRAFT_SYSTEM_PROMPT
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    # PII scrubbed before the LLM call
    user_content = kwargs["messages"][0]["content"]
    assert "123-45-6789" not in user_content
    assert "[REDACTED_SSN]" in user_content


@pytest.mark.asyncio
async def test_generate_draft_retries_once_then_succeeds() -> None:
    email = _email()
    client = AsyncMock()
    ok = MagicMock()
    ok.parsed_output = _ok_draft()
    ok.usage = MagicMock(input_tokens=10, output_tokens=5)
    client.messages.parse = AsyncMock(
        side_effect=[
            DraftGenerationError("empty"),
            ok,
        ]
    )

    result = await draft_llm.generate_draft(
        email,
        _context(email),
        _triage(),
        client=client,
        settings=_settings(),
    )
    assert result.draft.urgency == "NORMAL"
    assert client.messages.parse.await_count == 2


@pytest.mark.asyncio
async def test_generate_draft_double_failure_raises_draft_generation_error() -> None:
    email = _email()
    client = AsyncMock()
    client.messages.parse = AsyncMock(side_effect=ValueError("bad json"))

    with pytest.raises(DraftGenerationError, match="failed after retry"):
        await draft_llm.generate_draft(
            email,
            _context(email),
            _triage(),
            client=client,
            settings=_settings(),
        )
    assert client.messages.parse.await_count == 2


@pytest.mark.asyncio
async def test_generate_draft_missing_api_key_raises_clear_error() -> None:
    email = _email()
    client = AsyncMock()
    settings = Settings(
        draft_model="claude-sonnet-4-6",
        anthropic_api_key="",
    )

    with pytest.raises(DraftGenerationError, match="ANTHROPIC_API_KEY is not set"):
        await draft_llm.generate_draft(
            email,
            _context(email),
            _triage(),
            client=client,
            settings=settings,
        )
    client.messages.parse.assert_not_awaited()


@pytest.mark.asyncio
async def test_generate_draft_logs_metadata_only(monkeypatch: pytest.MonkeyPatch) -> None:
    email = _email()
    client = AsyncMock()
    parsed = MagicMock()
    parsed.parsed_output = _ok_draft()
    parsed.usage = MagicMock(input_tokens=11, output_tokens=7)
    client.messages.parse = AsyncMock(return_value=parsed)

    captured: list[tuple[str, dict[str, object]]] = []

    def _info(event: str, **kwargs: object) -> None:
        captured.append((event, kwargs))

    monkeypatch.setattr(draft_llm.logger, "info", _info)

    await draft_llm.generate_draft(
        email,
        _context(email),
        _triage(),
        client=client,
        settings=_settings(),
    )

    assert captured
    event, kwargs = captured[0]
    assert event == "draft_complete"
    joined = " ".join(f"{k}={v}" for k, v in kwargs.items())
    assert email.body_text not in joined
    assert "Happy to send the intake packet today." not in joined
    assert "vendor@example.com" not in joined


def _calendar_triage() -> TriageResultSchema:
    return TriageResultSchema(
        is_spam=False,
        spam_reason=None,
        has_action_items=True,
        action_items_summary="RSVP to IDME demo in Calendar",
        needs_context=False,
        context_reason=None,
        draft_needed=False,
    )


def _ok_briefing() -> BriefingSchema:
    return BriefingSchema(
        subject_line="Invitation: IDME's Demo - 2nd Week",
        teaching_note="RSVP in Calendar; do not email a reply.",
        urgency="NORMAL",
        urgency_reason="Scheduled meeting with no hard operational deadline.",
        suggested_actions=[
            {
                "step": 1,
                "action": "Accept or decline in Calendar",
                "stakeholder": "Elise",
                "rationale": "The ask is an RSVP, not an email.",
            }
        ],
    )


@pytest.mark.asyncio
async def test_generate_draft_without_draft_needed_uses_briefing_schema() -> None:
    email = _email().model_copy(
        update={
            "sender": "siddiq@sample-partner.example.com",
            "sender_display_name": "Abu Bakkar Siddiq",
            "subject": "Invitation: IDME's Demo - 2nd Week",
            "body_text": "Join with Google Meet",
            "meeting_message_type": "meetingRequest",
        }
    )
    client = AsyncMock()
    parsed = MagicMock()
    parsed.parsed_output = _ok_briefing()
    parsed.usage = MagicMock(input_tokens=40, output_tokens=20)
    client.messages.parse = AsyncMock(return_value=parsed)
    client.messages.create = AsyncMock()

    result = await draft_llm.generate_draft(
        email,
        _context(email),
        _calendar_triage(),
        client=client,
        settings=_settings(),
        skills=["You must always attach the rate card."],
        reference_loader=AsyncMock(),
    )

    assert result.draft.reply_body == ""
    assert result.draft.teaching_note == "RSVP in Calendar; do not email a reply."
    assert result.draft.suggested_recipients == []
    assert result.draft.forward_to is None
    kwargs = client.messages.parse.await_args.kwargs

    assert kwargs["output_format"] is BriefingSchema
    assert "reply_body" not in BriefingSchema.model_fields
    assert kwargs["max_tokens"] < draft_llm.DRAFT_MAX_TOKENS
    assert kwargs["system"][0]["text"] == BRIEFING_SYSTEM_PROMPT
    user_content = kwargs["messages"][0]["content"]
    system_text = kwargs["system"][0]["text"]
    assert "reply_body" not in user_content
    assert "reply_body" not in system_text
    assert "Hi {name}" not in user_content
    assert "Hi Abu" not in user_content
    assert "Sign the reply as" not in user_content
    assert "Reply addressee" not in user_content
    assert "rate card" not in user_content
    client.messages.create.assert_not_called()
