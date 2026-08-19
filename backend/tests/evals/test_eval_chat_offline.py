"""Offline specs for InboxAssistant RAG eval gold (no DeepEval/RAGAS, no live LLM).

Breaks if the chat dataset loses the worked examples (billing, Ashley,
info mailbox, write-refusal, no-match) or if the adapter feeds the judge
the wrong contexts / canned answers.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.llm.chat_prompts import NO_MATCH_ANSWER, WRITE_REFUSAL_ANSWER
from tests.evals.adapters.chat import (
    hits_from_case,
    retrieval_contexts_from_hits,
    run_chat_case,
)
from tests.evals.dataset_io import (
    CHAT_DATASET_V1,
    chat_cases_for_suite,
    list_chat_v1_cases,
    load_case,
)

INFO = "info@sample-services.example.com"
BILLING_THREAD = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
ASHLEY_THREAD = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
INFO_THREAD = "11111111-1111-1111-1111-111111111111"


def test_chat_v1_dataset_has_worked_examples() -> None:
    ids = {c["id"] for c in list_chat_v1_cases()}
    assert ids == {
        "chat-keyword-billing",
        "chat-person-ashley",
        "chat-info-mailbox-latest",
        "chat-write-refusal",
        "chat-no-match",
    }


def test_chat_suite_tags_partition() -> None:
    assert {c["id"] for c in chat_cases_for_suite("A")} == {
        "chat-keyword-billing",
        "chat-person-ashley",
        "chat-info-mailbox-latest",
    }
    assert {c["id"] for c in chat_cases_for_suite("B")} == {
        "chat-keyword-billing",
        "chat-person-ashley",
        "chat-info-mailbox-latest",
    }
    assert {c["id"] for c in chat_cases_for_suite("C")} == {
        "chat-write-refusal",
        "chat-no-match",
    }


def test_billing_gold_hit_is_the_overdue_invoice_thread() -> None:
    case = load_case(CHAT_DATASET_V1 / "keyword_billing.json")
    hits = hits_from_case(case)
    assert len(hits) == 1
    assert str(hits[0].thread_id) == BILLING_THREAD
    assert hits[0].mailbox == "sales@example.com"
    assert hits[0].subject == "Invoice dispute — overdue billing"
    assert hits[0].state == "REQUIRES_HUMAN"
    assert hits[0].urgency == "HIGH"
    blob = "\n".join(retrieval_contexts_from_hits(hits))
    assert "overdue billing packet" in blob
    assert BILLING_THREAD not in blob


def test_ashley_gold_hit_names_the_person() -> None:
    case = load_case(CHAT_DATASET_V1 / "person_ashley.json")
    hits = hits_from_case(case)
    assert len(hits) == 1
    assert str(hits[0].thread_id) == ASHLEY_THREAD
    blob = "\n".join(retrieval_contexts_from_hits(hits))
    assert "Ashley Cantrell" in blob
    assert "Stronger Together" in blob


def test_info_mailbox_gold_retrieval_is_only_info() -> None:
    """Hand count: 1 info@ hit; sales 'drug screen packet' must not appear."""
    case = load_case(CHAT_DATASET_V1 / "info_mailbox_latest.json")
    hits = hits_from_case(case)
    assert [h.mailbox for h in hits] == [INFO]
    assert str(hits[0].thread_id) == INFO_THREAD
    blob = "\n".join(retrieval_contexts_from_hits(hits))
    assert "Background order confirmation" in blob
    assert "background order for Friday" in blob
    assert "drug screen packet" not in blob.lower()
    assert "sales@example.com" not in blob


@pytest.mark.asyncio
async def test_write_refusal_case_uses_canned_read_only_answer() -> None:
    case = load_case(CHAT_DATASET_V1 / "write_refusal.json")
    with patch(
        "app.services.chat_service.run_chat_agent",
        AsyncMock(side_effect=AssertionError("write-intent must not call Claude")),
    ):
        payload = await run_chat_case(case, generate=True, anthropic_client=MagicMock())
    assert payload.refused_write is True
    assert WRITE_REFUSAL_ANSWER in payload.actual_output
    assert payload.retrieval_count == 1
    assert str(payload.hits[0].thread_id) == BILLING_THREAD


@pytest.mark.asyncio
async def test_no_match_case_uses_canned_empty_answer() -> None:
    case = load_case(CHAT_DATASET_V1 / "no_match.json")
    payload = await run_chat_case(case, generate=True, anthropic_client=MagicMock())
    assert payload.refused_write is False
    assert payload.actual_output == NO_MATCH_ANSWER
    assert payload.retrieval_count == 0
    assert payload.retrieval_contexts == []
    assert payload.hits == []
