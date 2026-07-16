"""Unit tests for deterministic LLM-egress PII scrubbing."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import Settings
from app.llm import triage as triage_llm
from app.llm.pii_redact import (
    TOKEN_BANK,
    TOKEN_CARD,
    TOKEN_DL,
    TOKEN_DOB,
    TOKEN_ID,
    TOKEN_SSN,
    scrub_email_for_llm,
    scrub_text,
    scrub_thread_for_llm,
)
from app.models.schemas.classification import TriageResultSchema
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema, ThreadContextSchema


def test_scrub_ssn_with_dashes() -> None:
    assert TOKEN_SSN in scrub_text("Applicant SSN is 123-45-6789 on file.")
    assert "123-45-6789" not in scrub_text("Applicant SSN is 123-45-6789 on file.")


def test_scrub_labeled_ssn_digits() -> None:
    text = scrub_text("SSN: 123456789 please process")
    assert TOKEN_SSN in text
    assert "123456789" not in text


def test_scrub_dob_labeled() -> None:
    text = scrub_text("DOB: 03/15/1988 and ready for review")
    assert TOKEN_DOB in text
    assert "03/15/1988" not in text


def test_scrub_dob_born_on_long_form() -> None:
    text = scrub_text("Driver was born on January 5, 1990 per the packet.")
    assert TOKEN_DOB in text
    assert "January 5, 1990" not in text


def test_scrub_driver_license_labeled() -> None:
    text = scrub_text("DL#: D1234567 expires next year")
    assert TOKEN_DL in text
    assert "D1234567" not in text


def test_scrub_cdl_labeled() -> None:
    text = scrub_text("CDL: CA99887766 on the MVR")
    assert TOKEN_DL in text
    assert "CA99887766" not in text


def test_scrub_routing_and_account() -> None:
    text = scrub_text("Routing: 021000021 Account number: 123456789012")
    assert TOKEN_BANK in text
    assert "021000021" not in text
    assert "123456789012" not in text


def test_scrub_iban() -> None:
    text = scrub_text("Wire to GB82WEST12345698765432 please")
    assert TOKEN_BANK in text
    assert "GB82WEST12345698765432" not in text


def test_scrub_luhn_card() -> None:
    # Visa test PAN that passes Luhn
    text = scrub_text("Card on file 4111-1111-1111-1111 for refund")
    assert TOKEN_CARD in text
    assert "4111-1111-1111-1111" not in text


def test_scrub_passport_and_ein_labeled() -> None:
    text = scrub_text("Passport: X12345678 EIN: 12-3456789 for background check")
    assert TOKEN_ID in text
    assert "X12345678" not in text
    assert "12-3456789" not in text


def test_ops_email_without_pii_unchanged() -> None:
    body = (
        "The driver's drug screen came back positive — what are our next steps? "
        "Court date is set for next Tuesday."
    )
    assert scrub_text(body) == body


def test_bare_nine_digits_not_scrubbed_without_label() -> None:
    # Avoid false positives on message ids / phone fragments.
    body = "Reference number 123456789 is for the shipment."
    assert scrub_text(body) == body


def test_scrub_email_for_llm_copies_and_preserves_recipients() -> None:
    email = EmailMessageSchema(
        message_id="m1",
        conversation_id="c1",
        mailbox="elise@example.com",
        sender="vendor@example.com",
        subject="SSN 123-45-6789 onboarding",
        body_text="Please file SSN: 987-65-4321 for the hire.",
        body_preview="Please file SSN: 987-65-4321",
        received_at=datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
        direction=EmailDirectionEnum.INBOUND,
        to_recipients=["elise@example.com"],
        cc_recipients=["ops@example.com"],
    )
    scrubbed = scrub_email_for_llm(email)
    assert scrubbed is not email
    assert email.subject == "SSN 123-45-6789 onboarding"
    assert "123-45-6789" in email.body_text or "987-65-4321" in email.body_text
    assert TOKEN_SSN in scrubbed.subject
    assert TOKEN_SSN in scrubbed.body_text
    assert scrubbed.body_preview is not None and TOKEN_SSN in scrubbed.body_preview
    assert scrubbed.to_recipients == ["elise@example.com"]
    assert scrubbed.sender == "vendor@example.com"


def test_scrub_thread_for_llm() -> None:
    msg = EmailMessageSchema(
        message_id="m1",
        conversation_id="c1",
        mailbox="elise@example.com",
        sender="a@example.com",
        subject="File",
        body_text="DOB: 01/02/1990",
        received_at=datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
    )
    thread = ThreadContextSchema(
        conversation_id="c1",
        mailbox="elise@example.com",
        subject="File with SSN 111-22-3333",
        messages=[msg],
    )
    scrubbed = scrub_thread_for_llm(thread)
    assert TOKEN_SSN in scrubbed.subject
    assert TOKEN_DOB in scrubbed.messages[0].body_text
    assert "01/02/1990" in thread.messages[0].body_text


@pytest.mark.asyncio
async def test_triage_email_payload_is_scrubbed() -> None:
    email = EmailMessageSchema(
        message_id="m1",
        conversation_id="c1",
        mailbox="elise@example.com",
        sender="vendor@example.com",
        subject="Background packet",
        body_text=(
            "New hire packet. SSN: 123-45-6789. DOB: 04/01/1985. "
            "DL#: D9988776. Drug screen came back positive."
        ),
        body_preview="New hire packet",
        received_at=datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
        direction=EmailDirectionEnum.INBOUND,
        to_recipients=["elise@example.com"],
    )
    thread = ThreadContextSchema(
        conversation_id=email.conversation_id,
        mailbox=email.mailbox,
        subject=email.subject,
        messages=[email],
    )
    client = AsyncMock()
    parsed = MagicMock()
    parsed.parsed_output = TriageResultSchema(
        is_spam=False,
        spam_reason=None,
        has_action_items=True,
        action_items_summary="Process hire packet",
        needs_context=False,
        context_reason=None,
    )
    parsed.usage = MagicMock(input_tokens=50, output_tokens=20)
    client.messages.parse = AsyncMock(return_value=parsed)

    await triage_llm.triage_email(
        client=client,
        settings=Settings(
            classification_model="claude-haiku-4-5",
            triage_max_tokens=200,
            anthropic_api_key="test-key",
        ),
        email=email,
        thread_context=thread,
    )

    user_content = client.messages.parse.await_args.kwargs["messages"][0]["content"]
    assert "123-45-6789" not in user_content
    assert "04/01/1985" not in user_content
    assert "D9988776" not in user_content
    assert TOKEN_SSN in user_content
    assert TOKEN_DOB in user_content
    assert TOKEN_DL in user_content
    assert "drug screen came back positive" in user_content.lower()
    # Original objects untouched for human review / storage.
    assert "123-45-6789" in email.body_text
