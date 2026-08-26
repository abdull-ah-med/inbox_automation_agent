"""Graph → ingest body mapping for review display.

Oracles are the literals Graph would return (whitespace body, uniqueBody, HTML).
A mapping that keeps whitespace as the body, drops uniqueBody, or stores HTML
tags in unique_body_text must fail these tests.
"""

from __future__ import annotations

from app.models.schemas.graph import GraphMessageSchema
from app.services.ingestion_service import _to_email_message_schema

MAILBOX = "sales@example.com"
CONVERSATION_ID = "conv-deploy-1"

OUTLOOK_QUOTED_TAIL = (
    "From: Alice <alice@example.com>\n"
    "Sent: Monday, August 22, 2022 10:43 AM\n"
    "To: sales@example.com\n"
    "Subject: Re: Vercel deploy\n\n"
    "Can you check the Vercel deploy?"
)
FULL_REPLY_WITH_QUOTE = f"Will do.\n\n{OUTLOOK_QUOTED_TAIL}"


def _graph_message(**overrides: object) -> GraphMessageSchema:
    payload: dict[str, object] = {
        "id": "AAMk-msg-1",
        "subject": "Re: Vercel deploy",
        "bodyPreview": "Will do.",
        "body": {"contentType": "text", "content": FULL_REPLY_WITH_QUOTE},
        "from": {"emailAddress": {"name": "Sales", "address": MAILBOX}},
        "receivedDateTime": "2026-08-22T14:43:00Z",
        "conversationId": CONVERSATION_ID,
    }
    payload.update(overrides)
    return GraphMessageSchema.model_validate(payload)


def _to_email(message: GraphMessageSchema):
    return _to_email_message_schema(
        mailbox=MAILBOX,
        conversation_id=CONVERSATION_ID,
        message=message,
    )


def test_whitespace_graph_body_falls_back_to_preview() -> None:
    email = _to_email(
        _graph_message(
            bodyPreview="Thanks, shipped.",
            body={"contentType": "text", "content": "  \n"},
        )
    )

    assert email.body_text == "Thanks, shipped."


def test_graph_schema_parses_unique_body() -> None:
    message = _graph_message(
        uniqueBody={"contentType": "text", "content": "Will do."},
    )

    assert message.unique_body is not None
    assert message.unique_body.content == "Will do."
    assert message.unique_body.content_type == "text"


def test_unique_body_is_new_reply_not_quoted_history() -> None:
    email = _to_email(
        _graph_message(
            uniqueBody={"contentType": "text", "content": "Will do."},
        )
    )

    assert email.unique_body_text == "Will do."
    assert "From: Alice" in email.body_text
    assert email.body_text == FULL_REPLY_WITH_QUOTE


def test_html_unique_body_is_stored_as_plain_text() -> None:
    email = _to_email(
        _graph_message(
            uniqueBody={"contentType": "html", "content": "<p>Will do.</p>"},
        )
    )

    assert email.unique_body_text == "Will do."
    assert "<" not in email.unique_body_text


def test_quote_only_body_without_unique_body_has_empty_unique_text() -> None:
    email = _to_email(
        _graph_message(
            bodyPreview="",
            body={"contentType": "text", "content": OUTLOOK_QUOTED_TAIL},
        )
    )

    assert email.unique_body_text == ""
    assert "From: Alice" in email.body_text
