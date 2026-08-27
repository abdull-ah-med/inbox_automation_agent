"""Chat retrieval query: follow-ups reuse the last contentful ask."""

import uuid

from app.models.schemas.chat import ChatHistoryTurn
from app.services.chat_query import retrieval_message


def test_contentful_ask_is_used_as_the_search_query() -> None:
    assert (
        retrieval_message("billing disputes waiting on review", ())
        == "billing disputes waiting on review"
    )


def test_filler_follow_up_reuses_last_user_content_query() -> None:
    history = (
        ChatHistoryTurn(role="user", content="billing disputes waiting on review"),
        ChatHistoryTurn(
            role="assistant",
            content="The overdue billing dispute is waiting on review.",
        ),
    )
    assert retrieval_message("tell me more", history) == ("billing disputes waiting on review")


def test_new_topic_does_not_reuse_prior_query() -> None:
    history = (ChatHistoryTurn(role="user", content="billing disputes waiting on review"),)
    assert retrieval_message("threads about SampleLab", history) == ("threads about SampleLab")


def test_overview_ask_without_history_stays_the_overview_phrase() -> None:
    assert retrieval_message("what should I focus on today?", ()) == (
        "what should I focus on today?"
    )


def test_first_one_resolves_to_the_first_cited_thread() -> None:
    from app.models.schemas.chat import ChatCitedThread
    from app.services.chat_query import resolve_cited_thread

    first = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    second = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    history = (
        ChatHistoryTurn(
            role="assistant",
            content="Two matches.",
            citations=[
                ChatCitedThread(thread_id=first, subject="Invoice dispute"),
                ChatCitedThread(thread_id=second, subject="Past due"),
            ],
        ),
    )
    assert resolve_cited_thread("the first one", history) == first


def test_that_thread_resolves_to_the_only_cited_thread() -> None:
    from app.models.schemas.chat import ChatCitedThread
    from app.services.chat_query import resolve_cited_thread

    only = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    history = (
        ChatHistoryTurn(
            role="assistant",
            content="One match.",
            citations=[ChatCitedThread(thread_id=only, subject="Invoice dispute")],
        ),
    )
    assert resolve_cited_thread("that thread", history) == only


def test_ordinal_without_citations_resolves_to_none() -> None:
    from app.services.chat_query import resolve_cited_thread

    assert resolve_cited_thread("the first one", ()) is None
