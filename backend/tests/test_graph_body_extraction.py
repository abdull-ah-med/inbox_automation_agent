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


# Graph uniqueBody ≈ full Zendesk notification (conversation-diff fails for ticket dumps).
_ZENDESK_UNIQUE_WALL = (
    "Your request (40197) has been updated. To add additional comments, "
    "reply to this email.\n\n"
    "Alex Taylor (SampleHelpdesk)\n\n"
    "Aug 25, 2026, 8:55 AM MDT\n\n"
    "Elise,\n\n"
    "Can you provide a search ID that I can look into?\n\n"
    "Alex Taylor\n"
    "Customer Support\n\n"
    "[https://sample-helpdesk.example.com/images/2016/default-avatar-80.png]\n\n"
    "info\n\n"
    "Aug 25, 2026, 8:19 AM MDT\n\n"
    "This is a follow-up to your previous request #40155\n\n"
    "Hi there.\n\n"
    "I updated the method for delivery last week.\n\n"
    "Thanks,\n"
    "Elise\n"
)


def test_zendesk_graph_unique_body_is_quote_split_to_newest_comment() -> None:
    """Graph uniqueBody still embeds prior ticket comments — persist tip only."""
    email = _to_email(
        _graph_message(
            body={"contentType": "text", "content": _ZENDESK_UNIQUE_WALL},
            uniqueBody={"contentType": "text", "content": _ZENDESK_UNIQUE_WALL},
            bodyPreview="Your request (40197) has been updated.",
        )
    )

    assert "search ID that I can look into" in email.unique_body_text
    assert "Alex Taylor" in email.unique_body_text
    assert "follow-up to your previous request" not in email.unique_body_text
    assert "I updated the method for delivery" not in email.unique_body_text
    assert "Thanks,\nElise" not in email.unique_body_text


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


def test_graph_schema_parses_meeting_message_fields() -> None:
    message = GraphMessageSchema.model_validate(
        {
            "@odata.type": "#microsoft.graph.eventMessageResponse",
            "id": "AAMk-meeting-1",
            "subject": "Accepted: 365BGC Web site review",
            "bodyPreview": "",
            "body": {"contentType": "text", "content": ""},
            "meetingMessageType": "meetingAccepted",
            "responseType": "accepted",
            "from": {"emailAddress": {"name": "Elise", "address": MAILBOX}},
            "receivedDateTime": "2026-08-26T15:39:54Z",
            "conversationId": CONVERSATION_ID,
        }
    )

    assert message.odata_type == "#microsoft.graph.eventMessageResponse"
    assert message.meeting_message_type == "meetingAccepted"
    assert message.response_type == "accepted"


def test_meeting_accepted_is_mapped_onto_email_schema() -> None:
    email = _to_email(
        GraphMessageSchema.model_validate(
            {
                "@odata.type": "#microsoft.graph.eventMessageResponse",
                "id": "AAMk-meeting-2",
                "subject": "Accepted: IDME - Quick Sync",
                "bodyPreview": "",
                "body": {"contentType": "text", "content": ""},
                "meetingMessageType": "meetingAccepted",
                "responseType": "accepted",
                "from": {"emailAddress": {"name": "Elise", "address": MAILBOX}},
                "receivedDateTime": "2026-08-20T16:05:08Z",
                "conversationId": CONVERSATION_ID,
            }
        )
    )

    assert email.meeting_message_type == "meetingAccepted"
    assert email.meeting_response_type == "accepted"
    assert email.body_text == ""


def test_ingest_keeps_graph_display_name_and_list_unsubscribe_as_automated() -> None:
    email = _to_email(
        GraphMessageSchema.model_validate(
            {
                "id": "AAMk-phmsa-1",
                "subject": "Registration is open for the 2026 PHMSA Hazmat Multimodal Event",
                "bodyPreview": "Join us",
                "body": {"contentType": "text", "content": "Join us"},
                "from": {
                    "emailAddress": {
                        "name": "PHMSA Subscriptions",
                        "address": "phmsa.subscriptions@info.dot.gov",
                    }
                },
                "internetMessageHeaders": [
                    {
                        "name": "List-Unsubscribe",
                        "value": (
                            "<https://public.govdelivery.com/accounts/USPHMSA/unsubscriber/new>"
                        ),
                    }
                ],
                "receivedDateTime": "2026-08-28T17:30:43Z",
                "conversationId": CONVERSATION_ID,
            }
        )
    )

    assert email.sender == "phmsa.subscriptions@info.dot.gov"
    assert email.sender_display_name == "PHMSA Subscriptions"
    assert email.is_automated is True


def test_ingest_keeps_person_display_name_and_calendar_is_not_automated() -> None:
    email = _to_email(
        GraphMessageSchema.model_validate(
            {
                "id": "AAMk-cal-1",
                "subject": "Invitation: IDME's Demo - 2nd Week",
                "bodyPreview": "Join with Google Meet",
                "body": {"contentType": "text", "content": "Join with Google Meet"},
                "from": {
                    "emailAddress": {
                        "name": "Abu Bakkar Siddiq",
                        "address": "siddiq@sample-partner.example.com",
                    }
                },
                "meetingMessageType": "meetingRequest",
                "receivedDateTime": "2026-08-28T06:27:05Z",
                "conversationId": CONVERSATION_ID,
            }
        )
    )

    assert email.sender == "siddiq@sample-partner.example.com"
    assert email.sender_display_name == "Abu Bakkar Siddiq"
    assert email.is_automated is False
    assert email.meeting_message_type == "meetingRequest"
