"""User-text sanitization: HTML, NULs, and control chars never survive."""

from app.core.sanitize import sanitize_user_text


def test_sanitize_user_text_strips_html_tags() -> None:
    assert sanitize_user_text("<b>SampleH</b>") == "SampleH"
    assert sanitize_user_text('<img src=x onerror="alert(1)">packet') == "packet"


def test_sanitize_user_text_strips_null_and_control_chars() -> None:
    assert sanitize_user_text("  SampleH\x00\x07  ") == "SampleH"


def test_sanitize_user_text_collapses_whitespace() -> None:
    assert sanitize_user_text("SampleH\n\n  order") == "SampleH order"
