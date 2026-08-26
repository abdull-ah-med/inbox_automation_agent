"""Quote/signature stripping for embedding documents — worked examples.

Oracle: hand-stripped bodies. A production change that leaves quoted history
in the search document must fail these tests.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.core.email_quotes import (
    EMBED_CLEAN_VERSION,
    QUOTE_PATTERN_SOURCES,
    find_quote_boundary,
    split_quoted_history,
    strip_quoted_reply,
)
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema
from app.services import embedding_service


def _gmail_thread_newest_reply() -> str:
    """12-message Gmail reply: newest body sits above the full quoted chain."""
    quoted = "\n".join(
        f"> On day {i} Alice wrote:\n> invoice 1 line {i} of quoted history"
        for i in range(11)
    )
    return (
        "Please send invoice 42 today.\n\n"
        "On Thu, Aug 14, 2026 at 3:00 PM Alice <alice@example.com> wrote:\n"
        f"{quoted}\n"
    )


def test_gmail_on_wrote_keeps_only_newest_reply() -> None:
    result = strip_quoted_reply(_gmail_thread_newest_reply())

    assert result.text == "Please send invoice 42 today."
    assert "invoice 1" not in result.text
    assert "wrote:" not in result.text.lower()
    assert result.quotes_stripped is True


def test_find_quote_boundary_gmail_marker_index() -> None:
    prefix = "Please send invoice 42 today.\n\n"
    body = (
        prefix
        + "On Thu, Aug 14, 2026 at 3:00 PM Alice <alice@example.com> wrote:\n"
        + "> invoice 1 from last week\n"
    )
    at = find_quote_boundary(body)
    assert at is not None
    assert "On Thu," in body[at:]
    assert body[:at].rstrip() == "Please send invoice 42 today."


def test_find_quote_boundary_outlook_marker_index() -> None:
    prefix = "Confirm the screen for Ashley Cantrell.\n\n"
    body = (
        prefix
        + "-----Original Message-----\n"
        + "From: bob@example.com\n"
        + "Quoted history about invoice 1 that should vanish\n"
    )
    at = find_quote_boundary(body)
    assert at is not None
    assert "Original Message" in body[at:]
    assert body[:at].rstrip() == "Confirm the screen for Ashley Cantrell."


def test_outlook_original_message_stripped() -> None:
    body = (
        "Confirm the screen for Ashley Cantrell.\n\n"
        "-----Original Message-----\n"
        "From: bob@example.com\n"
        "Quoted history about invoice 1 that should vanish\n"
    )
    result = strip_quoted_reply(body)

    assert result.text == "Confirm the screen for Ashley Cantrell."
    assert "invoice 1" not in result.text
    assert "Original Message" not in result.text
    assert result.quotes_stripped is True


def test_rfc3676_quoted_lines_stripped() -> None:
    body = (
        "New topic: drug screen for SampleClient 8/14.\n\n"
        "> old reply 1\n"
        "> old reply 2\n"
        ">> nested quote about invoice 1\n"
    )
    result = strip_quoted_reply(body)

    assert result.text == "New topic: drug screen for SampleClient 8/14."
    assert "invoice 1" not in result.text
    assert result.quotes_stripped is True


def test_inline_reply_above_on_wrote_is_kept() -> None:
    body = (
        "Sounds good — we will send the packet Friday.\n"
        "On Mon, Jan 1, 2024 at 10:00 AM Bob <bob@example.com> wrote:\n"
        "> Can you send the packet?\n"
    )
    result = strip_quoted_reply(body)

    assert result.text == "Sounds good — we will send the packet Friday."
    assert "Can you send the packet?" not in result.text


def test_rfc_signature_separator_stripped() -> None:
    body = "Please confirm the result.\n-- \nBest, Alice\nAlice Corp\n"
    result = strip_quoted_reply(body)

    assert result.text == "Please confirm the result."
    assert "Alice Corp" not in result.text
    assert result.signature_stripped is True


def test_quote_only_body_falls_back_to_original() -> None:
    body = "> quoted\n> more quoted\n"
    result = strip_quoted_reply(body)

    assert result.text == body
    assert result.quotes_stripped is False


def test_short_remainder_falls_back_to_original() -> None:
    body = "ok\n\nOn Mon Bob wrote:\n> long quoted history about invoice 1\n"
    result = strip_quoted_reply(body)

    assert result.text == body
    assert "invoice 1" in result.text


def test_embed_clean_version_is_at_least_two() -> None:
    """Quote-strip pass is a new embed version so reembed finds stale rows."""
    assert EMBED_CLEAN_VERSION >= 2


def test_build_search_document_omits_quoted_history() -> None:
    quoted = (
        "Please send invoice 42 today.\n\n"
        "On Thu, Aug 14, 2026 at 3:00 PM Alice <alice@example.com> wrote:\n"
        "> invoice 1 from last week\n"
    )
    email = EmailMessageSchema(
        message_id="graph-m1",
        conversation_id="c1",
        mailbox="elise@example.com",
        sender="vendor@example.com",
        subject="Invoice 42",
        body_text=quoted,
        body_clean=quoted,
        body_preview="Please send invoice 42 today.",
        received_at=datetime(2026, 8, 14, tzinfo=UTC),
        direction=EmailDirectionEnum.INBOUND,
        to_recipients=["elise@example.com"],
        cc_recipients=[],
    )
    doc = embedding_service.build_search_document(email)

    assert "invoice 42" in doc.lower()
    assert "invoice 1" not in doc.lower()
    assert "From: vendor@example.com" in doc
    assert "Subject: Invoice 42" in doc


def test_build_embed_text_omits_quoted_history() -> None:
    quoted = (
        "Please send invoice 42 today.\n\n"
        "-----Original Message-----\n"
        "From: bob@example.com\n"
        "Quoted history about invoice 1\n"
    )
    email = EmailMessageSchema(
        message_id="graph-m1",
        conversation_id="c1",
        mailbox="elise@example.com",
        sender="vendor@example.com",
        subject="Invoice 42",
        body_text=quoted,
        body_clean=quoted,
        received_at=datetime(2026, 8, 14, tzinfo=UTC),
        direction=EmailDirectionEnum.INBOUND,
        to_recipients=["elise@example.com"],
        cc_recipients=[],
    )
    text = embedding_service._build_embed_text(email, max_tokens=8000)

    assert "invoice 42" in text.lower()
    assert "invoice 1" not in text.lower()
    assert "Subject: Invoice 42" in text


@pytest.mark.parametrize(
    ("body", "expected_main"),
    [
        (
            "Please send invoice 42 today.\n\n"
            "On Thu, Aug 14, 2026 at 3:00 PM Alice <alice@example.com> wrote:\n"
            "> quoted history\n",
            "Please send invoice 42 today.",
        ),
        (
            "Confirm the screen.\n\n-----Original Message-----\nFrom: bob@example.com\nQuoted\n",
            "Confirm the screen.",
        ),
        (
            "Please confirm the result.\n-- \nBest, Alice\n",
            "Please confirm the result.\n-- \nBest, Alice\n",
        ),
    ],
)
def test_split_quoted_history_gmail_outlook_rfc_boundaries(
    body: str,
    expected_main: str,
) -> None:
    """RFC ``-- `` is a signature, not a quote split. Gmail/Outlook cut quoted history."""
    main, quoted = split_quoted_history(body)
    assert main == expected_main.rstrip() or main == expected_main
    if "Original Message" in body or "wrote:" in body:
        assert quoted is not None
    if "-- " in body and "Original Message" not in body and "wrote:" not in body:
        assert quoted is None


def test_frontend_quote_pattern_file_matches_python_sources() -> None:
    from pathlib import Path

    ts_path = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "web"
        / "src"
        / "lib"
        / "email-quote-patterns.ts"
    )
    text = ts_path.read_text()
    for name, source, _flags in QUOTE_PATTERN_SOURCES:
        assert source in text, name
