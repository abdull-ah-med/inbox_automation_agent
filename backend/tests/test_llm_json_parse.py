"""Tests for fence-tolerant LLM JSON parsing.

Oracle: a Haiku blob wrapped in markdown fences still yields the object.
Bare JSON still parses. Garbage returns None (best-effort).
"""

from __future__ import annotations

from app.llm.json_parse import parse_llm_json


def test_parse_llm_json_strips_json_fence() -> None:
    """Fenced Haiku output still yields the atoms object."""
    raw = '```json\n{"atoms": [{"text": "Lead with the invoice number", "role": "Fix"}]}\n```'
    obj = parse_llm_json(raw)
    assert obj == {
        "atoms": [{"text": "Lead with the invoice number", "role": "Fix"}],
    }


def test_parse_llm_json_strips_bare_fence() -> None:
    """A fence without a language tag still parses."""
    raw = '```\n{"applies": [true, false]}\n```'
    obj = parse_llm_json(raw)
    assert obj == {"applies": [True, False]}


def test_parse_llm_json_bare_object() -> None:
    """Unfenced JSON still parses."""
    obj = parse_llm_json('{"violations": []}')
    assert obj == {"violations": []}


def test_parse_llm_json_garbage_returns_none() -> None:
    """Non-JSON returns None rather than raising."""
    assert parse_llm_json("not json at all") is None
    assert parse_llm_json("") is None
    assert parse_llm_json("```json\nnot-json\n```") is None
