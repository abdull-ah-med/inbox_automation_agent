"""Offline regression tests for prompt assets.

These do NOT call any LLM (CI never touches external services). They lock the
*integrity* of the prompt assets: that the urgency taxonomy stays in sync with the
schema, that every level is documented, and that labeled examples stay well-formed.
When you change app/llm/prompts.py, update these expectations too.
"""

from collections import Counter
from typing import get_args

from app.llm.prompts import (
    DRAFT_SYSTEM_PROMPT,
    PROMPT_VERSION,
    TRIAGE_SYSTEM_PROMPT,
    URGENCY_EXAMPLES,
    URGENCY_LEVELS,
    URGENCY_TAXONOMY,
)
from app.models.schemas.classification import ClassificationSchema
from app.models.schemas.draft import DraftSchema


def test_urgency_levels_match_schema() -> None:
    """The prompt's urgency levels must equal the schema's Literal, exactly."""
    schema_levels = get_args(ClassificationSchema.model_fields["urgency"].annotation)
    draft_levels = get_args(DraftSchema.model_fields["urgency"].annotation)
    assert set(URGENCY_LEVELS) == set(schema_levels)
    assert set(URGENCY_LEVELS) == set(draft_levels)


def test_taxonomy_documents_every_level() -> None:
    for level in URGENCY_LEVELS:
        assert level in URGENCY_TAXONOMY, f"{level} missing from URGENCY_TAXONOMY"


def test_draft_prompt_embeds_taxonomy_and_levels() -> None:
    assert URGENCY_TAXONOMY in DRAFT_SYSTEM_PROMPT
    for level in URGENCY_LEVELS:
        assert level in DRAFT_SYSTEM_PROMPT


def test_triage_prompt_requests_json_only() -> None:
    lowered = TRIAGE_SYSTEM_PROMPT.lower()
    assert "json" in lowered
    assert "no preamble" in lowered or "do not add" in lowered


def test_triage_prompt_mentions_poi() -> None:
    assert "Person of Interest" in TRIAGE_SYSTEM_PROMPT or "PoI" in TRIAGE_SYSTEM_PROMPT
    assert "Elise" in TRIAGE_SYSTEM_PROMPT
    assert "Mailbox" in TRIAGE_SYSTEM_PROMPT


def test_triage_prompt_documents_output_fields() -> None:
    for field in (
        "is_spam",
        "has_action_items",
        "needs_context",
    ):
        assert field in TRIAGE_SYSTEM_PROMPT
    assert "confidence" not in TRIAGE_SYSTEM_PROMPT.lower()


def test_triage_prompt_is_non_empty() -> None:
    assert isinstance(TRIAGE_SYSTEM_PROMPT, str) and TRIAGE_SYSTEM_PROMPT.strip()


def test_examples_are_wellformed_and_cover_all_levels() -> None:
    counts = Counter(level for _text, level in URGENCY_EXAMPLES)
    for level in URGENCY_LEVELS:
        assert counts[level] >= 2, f"need >=2 examples for {level}, got {counts[level]}"
    # No stray levels outside the taxonomy.
    assert set(counts) == set(URGENCY_LEVELS)
    for text, _level in URGENCY_EXAMPLES:
        assert text.strip(), "example text must be non-empty"


def test_triage_prompt_documents_redaction_tokens() -> None:
    assert "[REDACTED_SSN]" in TRIAGE_SYSTEM_PROMPT
    assert "privacy" in TRIAGE_SYSTEM_PROMPT.lower() or "mask" in TRIAGE_SYSTEM_PROMPT.lower()


def test_prompt_version_present() -> None:
    assert isinstance(PROMPT_VERSION, str) and PROMPT_VERSION.strip()


def test_draft_prompt_requires_teaching_note() -> None:
    assert "teaching_note" in DRAFT_SYSTEM_PROMPT
    assert "REQUIRED" in DRAFT_SYSTEM_PROMPT


def test_draft_prompt_requires_urgency_fields() -> None:
    assert "urgency" in DRAFT_SYSTEM_PROMPT
    assert "urgency_reason" in DRAFT_SYSTEM_PROMPT
