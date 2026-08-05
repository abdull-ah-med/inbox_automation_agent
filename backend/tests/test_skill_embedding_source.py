"""Unit tests for skill embedding source string."""

from __future__ import annotations

from app.services.skill_embedding_service import build_embedding_source


def test_build_embedding_source_includes_name_description_and_body_head() -> None:
    body = "A" * 500
    text = build_embedding_source(
        name="samplelab-rebilling",
        description="Rebill SampleLab invoices correctly",
        body=body,
    )
    assert text.startswith("samplelab-rebilling")
    assert "Rebill SampleLab invoices correctly" in text
    assert "A" * 400 in text
    assert "A" * 401 not in text
    # Body head is truncated at 400 chars — total body contribution capped.
    assert text.count("A") == 400


def test_build_embedding_source_handles_missing_description() -> None:
    text = build_embedding_source(
        name="demo-skill",
        description=None,
        body="First line of the skill body.",
    )
    assert text == "demo-skill\n\nFirst line of the skill body."


def test_build_embedding_source_handles_empty_body() -> None:
    text = build_embedding_source(
        name="demo-skill",
        description="Only description",
        body="   ",
    )
    assert text == "demo-skill\n\nOnly description"
