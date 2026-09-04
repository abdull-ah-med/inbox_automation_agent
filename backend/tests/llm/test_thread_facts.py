"""Unit tests for ADD-only thread fact parse.

Oracles: only facts whose source_message_id is in the passed id set survive.
"""

from __future__ import annotations

import uuid

from app.llm.thread_facts import parse_thread_facts


def test_parse_thread_facts_drops_orphan_source_ids() -> None:
    known = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
    parsed = parse_thread_facts(
        (
            '{"facts":['
            '{"text":"Driver Ames check 11111","source_message_id":'
            '"aaaaaaaa-0000-0000-0000-000000000001"},'
            '{"text":"Hallucinated other thread","source_message_id":'
            '"bbbbbbbb-0000-0000-0000-000000000002"}'
            "]}"
        ),
        allowed_message_ids={known},
    )
    assert parsed == [
        {"text": "Driver Ames check 11111", "source_message_id": known},
    ]


def test_parse_thread_facts_rejects_empty_text() -> None:
    known = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
    parsed = parse_thread_facts(
        '{"facts":[{"text":"   ","source_message_id":"aaaaaaaa-0000-0000-0000-000000000001"}]}',
        allowed_message_ids={known},
    )
    assert parsed == []
