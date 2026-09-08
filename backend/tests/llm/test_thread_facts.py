"""Unit tests for ADD-only thread fact parse.

Oracles: only facts whose source_message_id is in the passed id set survive.
"""

from __future__ import annotations

import uuid

from app.llm.thread_facts import _pack_messages, parse_thread_facts


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


def test_pack_uses_salute_from_name_not_graph_role_label() -> None:
    """Haiku must not see From display 'Sample Developer' or local-part Dev."""
    from datetime import UTC, datetime
    from types import SimpleNamespace

    unsigned = SimpleNamespace(
        id=uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001"),
        direction="inbound",
        sender="Dev@sample-site.example.com",
        sender_name="Sample Developer",
        body_text="When I checked that sent email activity the button works.",
        body_clean=None,
        body_content_type="text",
        received_at=datetime(2026, 9, 4, 12, 39, tzinfo=UTC),
    )
    packed = _pack_messages(
        [unsigned],
        mailbox="sampleagent@sample-site.example.com",
        mailbox_owner="Elise",
    )
    assert "from_name=Sample Developer" not in packed
    assert "from_name=Sample" not in packed
    assert "from_name=Dev\n" not in packed
    assert "from_name=\n" in packed
    assert "from_email=Dev@sample-site.example.com" in packed

    signed = SimpleNamespace(
        id=unsigned.id,
        direction="inbound",
        sender="Dev@sample-site.example.com",
        sender_name="Sample Developer",
        body_text="Screens are updated.\n\nThanks,\nDivyansh\n",
        body_clean=None,
        body_content_type="text",
        received_at=datetime(2026, 9, 4, 12, 39, tzinfo=UTC),
    )
    signed_packed = _pack_messages([signed], mailbox="sampleagent@sample-site.example.com")
    assert "from_name=Divyansh" in signed_packed
    assert "from_name=Sample Developer" not in signed_packed


def test_pack_uses_directory_salute_when_from_name_empty() -> None:
    from datetime import UTC, datetime
    from types import SimpleNamespace

    unsigned = SimpleNamespace(
        id=uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001"),
        direction="inbound",
        sender="Dev@sample-site.example.com",
        sender_name="Sample Developer",
        body_text="When I checked that sent email activity the button works.",
        body_clean=None,
        body_content_type="text",
        received_at=datetime(2026, 9, 4, 12, 39, tzinfo=UTC),
    )
    packed = _pack_messages(
        [unsigned],
        mailbox="sampleagent@sample-site.example.com",
        directory={"dev@sample-site.example.com": "Divyansh"},
    )
    assert "from_name=Divyansh" in packed


def test_facts_prompt_forbids_inventing_name_from_empty_from_name() -> None:
    from app.llm.prompts import THREAD_FACTS_SYSTEM_PROMPT

    assert (
        "If from_name is empty, do not invent a name from from_email or its local-part"
        in THREAD_FACTS_SYSTEM_PROMPT
    )
