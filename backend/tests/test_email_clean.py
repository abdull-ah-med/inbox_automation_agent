"""Unit tests for email_clean (quotes, signatures, disclaimers, HTML)."""

from __future__ import annotations

from app.llm.email_clean import CLEAN_VERSION, clean_email_body


def test_clean_version_is_positive_int() -> None:
    assert isinstance(CLEAN_VERSION, int) and CLEAN_VERSION >= 1


def test_outlook_original_message_stripped() -> None:
    raw = (
        "Please send the packet today.\n\n"
        "-----Original Message-----\n"
        "From: bob@example.com\n"
        "Quoted history that should vanish\n"
    )
    result = clean_email_body(raw, content_type="text")
    assert "Please send the packet today." in result.body_clean
    assert "Original Message" not in result.body_clean
    assert result.quote_stripped is True
    assert result.clean_version == CLEAN_VERSION


def test_gmail_on_wrote_stripped() -> None:
    raw = (
        "Thanks for the update.\n\n"
        "On Mon, Jan 1, 2024 at 10:00 AM Bob <bob@example.com> wrote:\n"
        "> previous message body\n"
    )
    result = clean_email_body(raw, content_type="text")
    assert "Thanks for the update." in result.body_clean
    assert "wrote:" not in result.body_clean.lower()


def test_quoted_marker_lines_stripped_when_enough() -> None:
    # Talon needs at least 3 quotation-marker lines to strip.
    raw = "New reply here\n\n> old 1\n> old 2\n> old 3\n> old 4\n"
    result = clean_email_body(raw, content_type="text")
    assert "New reply here" in result.body_clean


def test_signature_bruteforce_stripped() -> None:
    raw = "Wow. Awesome!\n--\nBob Smith\n"
    result = clean_email_body(raw, content_type="text")
    assert "Wow. Awesome!" in result.body_clean
    assert "Bob Smith" not in result.body_clean
    assert result.signature_stripped is True


def test_corporate_disclaimer_stripped() -> None:
    raw = (
        "Please confirm the drug screen result.\n\n"
        "Thanks,\n"
        "Alex\n\n"
        "This email and any files transmitted with it are confidential "
        "and intended solely for the use of the individual or entity "
        "to whom they are addressed. If you have received this email "
        "in error please notify the system manager.\n"
    )
    result = clean_email_body(raw, content_type="text")
    assert "Please confirm the drug screen result." in result.body_clean
    assert "confidential" not in result.body_clean.lower()
    assert result.disclaimer_stripped is True


def test_html_gmail_quote_stripped() -> None:
    html = (
        "<div>Reply body only</div>"
        '<div class="gmail_quote">'
        "On Mon Bob wrote:<br>Quoted prior"
        "</div>"
    )
    result = clean_email_body(html, content_type="html")
    assert "Reply body only" in result.body_clean
    # Quoted prior should be gone or substantially reduced.
    assert "Quoted prior" not in result.body_clean


def test_empty_body_returns_empty_clean() -> None:
    result = clean_email_body("   ", content_type="text")
    assert result.body_clean == ""


def test_cleaner_never_raises_on_garbage() -> None:
    # Unbalanced / odd input should fall back rather than raise.
    result = clean_email_body("\x00\x01 still text", content_type="text")
    assert isinstance(result.body_clean, str)


def test_unknown_content_type_treated_as_text() -> None:
    result = clean_email_body("Hello there, please reply soon.", content_type="markdown")
    assert "Hello there" in result.body_clean


def test_disclaimer_long_tail_window() -> None:
    # Force the >40-line disclaimer search window (trigger near end of tail).
    head = "\n".join(f"Line {i} of legitimate body content here." for i in range(50))
    disclaimer = (
        "Closing note.\n"
        "This message contains confidential information and is intended "
        "solely for the use of the individual or entity to whom they are addressed."
    )
    result = clean_email_body(f"{head}\n\n{disclaimer}", content_type="text")
    assert "Line 0 of legitimate" in result.body_clean
    assert result.disclaimer_stripped is True
    assert "confidential information" not in result.body_clean.lower()


def test_effective_body_text_prefers_clean() -> None:
    from app.llm.email_clean import effective_body_text

    assert (
        effective_body_text(
            body_clean="cleaned",
            body_text="raw",
            body_content_type="text",
        )
        == "cleaned"
    )


def test_effective_body_text_falls_back_and_cleans() -> None:
    from app.llm.email_clean import effective_body_text

    text = effective_body_text(
        body_clean=None,
        body_text="Thanks\n\n-----Original Message-----\nQuoted",
        body_content_type="text",
    )
    assert "Thanks" in text
    assert "Original Message" not in text
