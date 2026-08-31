"""NL mailbox scope: peel 'info mailbox' into a filter, not an FTS AND token.

Oracles from the RAG plan: scope language is metadata; content keywords stay.
Known mailboxes are literals from a hand-built allowlist — not inferred from
production resolve helpers in the assertion.
"""

from __future__ import annotations

from app.services.nl_mailbox_scope import extract_nl_mailbox_scope

INFO = "info@sample-services.example.com"
INQUIRIES = "inquiries@sample-site.example.com"
SUPPORT = "support@sample-site.example.com"
ALLOWLIST = [INFO, INQUIRIES, SUPPORT]


def test_info_mailbox_phrase_becomes_mailbox_token_and_empty_content() -> None:
    """Break if 'info mailbox' stays in free text (FTS would AND mailbox:*)."""
    scoped = extract_nl_mailbox_scope(
        "what s the latest on the info mailbox",
        ALLOWLIST,
    )
    assert scoped.mailboxes == ("info",)
    assert scoped.free_text == "what s the latest on the"


def test_inquiries_inbox_phrase_scopes_without_eating_topic_keywords() -> None:
    scoped = extract_nl_mailbox_scope(
        "latest on the inquiries inbox drug screen packet",
        ALLOWLIST,
    )
    assert scoped.mailboxes == ("inquiries",)
    assert scoped.free_text == "latest on the drug screen packet"


def test_full_email_in_free_text_becomes_mailbox_scope() -> None:
    scoped = extract_nl_mailbox_scope(
        f"what is new in {INFO}",
        ALLOWLIST,
    )
    assert scoped.mailboxes == (INFO,)
    assert "sampleservices" not in scoped.free_text.lower()
    assert scoped.free_text == "what is new in"


def test_info_without_mailbox_noun_stays_content_keyword() -> None:
    """'latest on info' must keep info for keyword search (existing chatty ask)."""
    scoped = extract_nl_mailbox_scope(
        "give me the latest on info what s happening there",
        ALLOWLIST,
    )
    assert scoped.mailboxes == ()
    assert scoped.free_text == "give me the latest on info what s happening there"


def test_unknown_mailbox_phrase_is_not_invented() -> None:
    scoped = extract_nl_mailbox_scope(
        "latest on the finance mailbox",
        ALLOWLIST,
    )
    assert scoped.mailboxes == ()
    assert scoped.free_text == "latest on the finance mailbox"


def test_keyword_ask_unchanged() -> None:
    scoped = extract_nl_mailbox_scope(
        "billing disputes waiting on review",
        ALLOWLIST,
    )
    assert scoped.mailboxes == ()
    assert scoped.free_text == "billing disputes waiting on review"


def test_mailbox_noun_before_alias() -> None:
    scoped = extract_nl_mailbox_scope(
        "show me the mailbox support overview",
        ALLOWLIST,
    )
    assert scoped.mailboxes == ("support",)
    assert scoped.free_text == "show me the overview"
