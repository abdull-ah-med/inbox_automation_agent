"""Chat retrieval query: follow-ups reuse the last contentful ask."""

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
    assert retrieval_message("tell me more", history) == (
        "billing disputes waiting on review"
    )


def test_new_topic_does_not_reuse_prior_query() -> None:
    history = (ChatHistoryTurn(role="user", content="billing disputes waiting on review"),)
    assert retrieval_message("threads about SampleLab", history) == (
        "threads about SampleLab"
    )


def test_overview_ask_without_history_stays_the_overview_phrase() -> None:
    assert retrieval_message("what should I focus on today?", ()) == (
        "what should I focus on today?"
    )
