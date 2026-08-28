"""Deterministic opening-greeting rewrite for draft bodies (no LLM)."""

from __future__ import annotations

import pytest

from app.core.draft_salutation import format_opening_salutation, rewrite_opening_salutation


@pytest.mark.parametrize(
    ("salute_name", "expected"),
    [
        ("Kelvin", "Hi Kelvin,"),
        ("team", "Hi team,"),
        ("", "Hi,"),
        ("  ", "Hi,"),
    ],
)
def test_format_opening_salutation(salute_name: str, expected: str) -> None:
    assert format_opening_salutation(salute_name) == expected


def test_rewrite_replaces_named_hi_greeting() -> None:
    body = "Hi Samplecontact,\n\nThanks for reaching out.\n\nBest,\nElise\n"
    assert rewrite_opening_salutation(body, "Kelvin") == (
        "Hi Kelvin,\n\nThanks for reaching out.\n\nBest,\nElise\n"
    )


def test_rewrite_replaces_bare_hi() -> None:
    body = "Hi,\n\nThanks for the note.\n"
    assert rewrite_opening_salutation(body, "Kelvin") == "Hi Kelvin,\n\nThanks for the note.\n"


def test_rewrite_to_bare_hi_when_name_cleared() -> None:
    body = "Hi Kelvin,\n\nBody here.\n"
    assert rewrite_opening_salutation(body, "") == "Hi,\n\nBody here.\n"


def test_rewrite_hello_greeting_normalized_to_hi() -> None:
    body = "Hello Beau,\n\nChecking in.\n"
    assert rewrite_opening_salutation(body, "Kelvin") == "Hi Kelvin,\n\nChecking in.\n"


def test_rewrite_prepends_when_no_greeting_line() -> None:
    body = "Thanks for reaching out about pricing.\n"
    assert rewrite_opening_salutation(body, "Kelvin") == (
        "Hi Kelvin,\n\nThanks for reaching out about pricing.\n"
    )


def test_rewrite_preserves_rest_of_multiline_body() -> None:
    body = "Hi team,\nLine two\nLine three"
    out = rewrite_opening_salutation(body, "Alex")
    assert out == "Hi Alex,\nLine two\nLine three"
