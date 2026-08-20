"""Chat query intent: NL → retrieval plan before the LLM tool loop.

Oracles from the RAG research doc and Elise demo asks: mailbox overview
vs person/keyword search vs follow-up vs queue counts vs off-topic.
"""

from __future__ import annotations

import uuid

from app.models.schemas.chat import ChatCitedThread, ChatHistoryTurn
from app.services.chat_intent import ChatIntent, classify_chat_intent

INFO = "info@example.com"
SUPPORT = "support@example.com"
MAILBOXES = [INFO, SUPPORT]
THREAD_A = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
THREAD_B = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def test_latest_on_info_mailbox_is_overview_scoped_to_info() -> None:
    plan = classify_chat_intent(
        "what's the latest on the info mailbox",
        history=(),
        mailbox_emails=MAILBOXES,
    )
    assert plan.intent == ChatIntent.OVERVIEW
    assert plan.tool_name == "list_recent_threads"
    assert plan.mailbox == "info"


def test_focus_today_is_overview_across_mailboxes() -> None:
    plan = classify_chat_intent(
        "what should I focus on today?",
        history=(),
        mailbox_emails=MAILBOXES,
    )
    assert plan.intent == ChatIntent.OVERVIEW
    assert plan.tool_name == "list_recent_threads"
    assert plan.mailbox is None


def test_person_name_is_search() -> None:
    plan = classify_chat_intent(
        "Ashley Cantrell",
        history=(),
        mailbox_emails=MAILBOXES,
    )
    assert plan.intent == ChatIntent.SEARCH
    assert plan.tool_name == "search_mail"


def test_keyword_topic_is_search() -> None:
    plan = classify_chat_intent(
        "billing disputes waiting on review",
        history=(),
        mailbox_emails=MAILBOXES,
    )
    assert plan.intent == ChatIntent.SEARCH
    assert plan.tool_name == "search_mail"


def test_queue_count_is_aggregation() -> None:
    plan = classify_chat_intent(
        "how many threads are waiting",
        history=(),
        mailbox_emails=MAILBOXES,
    )
    assert plan.intent == ChatIntent.AGGREGATION
    assert plan.tool_name == "get_overview"


def test_weather_is_out_of_scope() -> None:
    plan = classify_chat_intent(
        "what's the weather",
        history=(),
        mailbox_emails=MAILBOXES,
    )
    assert plan.intent == ChatIntent.OUT_OF_SCOPE
    assert plan.tool_name is None


def test_poem_is_out_of_scope() -> None:
    plan = classify_chat_intent(
        "write me a poem",
        history=(),
        mailbox_emails=MAILBOXES,
    )
    assert plan.intent == ChatIntent.OUT_OF_SCOPE
    assert plan.tool_name is None


def test_first_one_follow_up_resolves_first_cited_thread() -> None:
    history = (
        ChatHistoryTurn(role="user", content="billing disputes waiting on review"),
        ChatHistoryTurn(
            role="assistant",
            content="Two billing threads need review.",
            citations=[
                ChatCitedThread(thread_id=THREAD_A, subject="Invoice dispute"),
                ChatCitedThread(thread_id=THREAD_B, subject="Past due notice"),
            ],
        ),
    )
    plan = classify_chat_intent(
        "tell me more about the first one",
        history=history,
        mailbox_emails=MAILBOXES,
    )
    assert plan.intent == ChatIntent.FOLLOW_UP
    assert plan.tool_name == "get_thread"
    assert plan.thread_id == THREAD_A
