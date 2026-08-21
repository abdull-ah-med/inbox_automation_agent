"""Mailbox owner map: email → display name for personal inboxes."""

from __future__ import annotations

from app.core.config import Settings
from app.core.mailbox_keys import mailbox_label, owner_for_mailbox


def test_owner_map_parses_email_colon_name_pairs() -> None:
    settings = Settings(
        environment="local",
        mailbox_owners="sampleagent@sample-site.example.com:Elise,ops@example.com:Ops Desk",
    )
    assert settings.mailbox_owner_map == {
        "sampleagent@sample-site.example.com": "Elise",
        "ops@example.com": "Ops Desk",
    }


def test_owner_for_mailbox_is_case_insensitive() -> None:
    owners = {"sampleagent@sample-site.example.com": "Elise"}
    assert owner_for_mailbox("Sampleagent@sample-site.example.com", owners) == "Elise"
    assert owner_for_mailbox("other@sample-site.example.com", owners) is None


def test_owner_for_mailbox_empty_map_returns_none() -> None:
    assert owner_for_mailbox("sampleagent@sample-site.example.com", {}) is None
    assert owner_for_mailbox("sampleagent@sample-site.example.com", None) is None


def test_mailbox_label_uses_owner_name_when_present() -> None:
    assert (
        mailbox_label(
            "sampleagent@sample-site.example.com",
            owners={"sampleagent@sample-site.example.com": "Elise"},
        )
        == "Elise"
    )


def test_mailbox_label_falls_back_to_local_part_without_owner() -> None:
    assert mailbox_label("sampleagent@sample-site.example.com", owners={}) == "Sampleagent"
