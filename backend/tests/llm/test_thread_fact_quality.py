"""Oracles for which thread facts are worth keeping.

User-visible junk from the first Haiku pass: signature dumps, "sent something",
family medical gossip, and "the integration is resolved" with no ticket/id.
"""

from types import SimpleNamespace

from app.llm.thread_fact_quality import (
    apply_person_aliases,
    is_useful_thread_fact,
    person_aliases_from_fact_texts,
    person_aliases_from_messages,
)


def test_rejects_signature_and_gossip_literals() -> None:
    assert not is_useful_thread_fact(
        "Elise Chouest is Director at SampleSite with email "
        "sampleagent@sample-site.example.com and phone (202) 555-0105 ext. 201."
    )
    assert not is_useful_thread_fact("Sample Helpdesk reported that the integration is resolved.")
    assert not is_useful_thread_fact(
        "Samaba Safety team sent something the week the recipient's son had appendicitis."
    )
    assert not is_useful_thread_fact(
        "sampleagent@sample-site.example.com is asking if help is available today."
    )


def test_rejects_helpdesk_and_signature_role_literals() -> None:
    assert not is_useful_thread_fact(
        "An applicant is experiencing site issues that need urgent resolution."
    )
    assert not is_useful_thread_fact(
        "Cydney Blumenthal is Director of Digital Services at SampleVendor."
    )
    assert not is_useful_thread_fact("Client reports security issues with the portal.")
    assert not is_useful_thread_fact(
        "Need workaround or resolution for appscreen invite deliverability "
        "issue or alternative manual submission path with known required documents."
    )


def test_keeps_named_product_and_counted_literals() -> None:
    assert is_useful_thread_fact(
        "SampleVendor cannot receive AppScreen invites in applicant inboxes "
        "and cannot open myApp without the code from that invite."
    )
    assert is_useful_thread_fact(
        "SampleVendor volume dropped from about 60 searches per week to none this week."
    )
    assert is_useful_thread_fact(
        "Cydney asked for a list of required documents (consents, disclosures) "
        "for manually submitted orders."
    )
    assert is_useful_thread_fact(
        "Dev asked Elise to check if payment records contain identifiers "
        "'F71-1781119712' or 'D86-1778001750'."
    )
    assert is_useful_thread_fact("Check 11111 is for driver Ames, cancelled.")
    assert is_useful_thread_fact("There are 3 payment records with method sync issues.")


def test_elises_email_becomes_elise_except_in_email_label() -> None:
    aliases = {"sampleagent@sample-site.example.com": "Elise"}
    rewritten = apply_person_aliases(
        "sampleagent@sample-site.example.com asked Dev whether they have pre-QBO data.",
        aliases,
    )
    assert rewritten == "Elise asked Dev whether they have pre-QBO data."
    identity = apply_person_aliases(
        "Elise Chouest is Director with email sampleagent@sample-site.example.com",
        aliases,
    )
    assert "sampleagent@sample-site.example.com" in identity
    assert identity.startswith("Elise Chouest")


def test_participant_first_name_from_from_header() -> None:
    from app.core.person_name import display_name_first_name

    assert display_name_first_name("Elise Chouest") == "Elise"
    assert display_name_first_name("SampleSite Support") is None

    message = SimpleNamespace(
        sender="sampleagent@sample-site.example.com",
        sender_name="Elise Chouest",
    )
    aliases = person_aliases_from_messages([message])
    assert aliases["sampleagent@sample-site.example.com"] == "Elise"


def test_identity_sentence_fills_alias_map() -> None:
    aliases = person_aliases_from_fact_texts(
        [
            "Elise Chouest is Director at SampleSite with email "
            "sampleagent@sample-site.example.com and phone (202) 555-0105 ext. 201."
        ]
    )
    assert aliases["sampleagent@sample-site.example.com"] == "Elise"


def test_role_mailbox_display_is_not_an_alias() -> None:
    """Sample Developer / Dev@ must not become the fact actor name."""
    message = SimpleNamespace(
        sender="Dev@sample-site.example.com",
        sender_name="Sample Developer",
        body_text="When I checked that sent email activity the button works.",
        direction="inbound",
    )
    aliases = person_aliases_from_messages([message])
    assert "dev@sample-site.example.com" not in aliases
    assert "Sample" not in aliases.values()

    signed = SimpleNamespace(
        sender="Dev@sample-site.example.com",
        sender_name="Sample Developer",
        body_text="Screens are updated.\n\nThanks,\nDivyansh\n",
        direction="inbound",
    )
    signed_aliases = person_aliases_from_messages([signed])
    assert signed_aliases["dev@sample-site.example.com"] == "Divyansh"


def test_role_title_identity_sentence_is_not_an_alias() -> None:
    aliases = person_aliases_from_fact_texts(
        ["Sample Developer is on the engineering mailbox with email dev@sample-site.example.com"]
    )
    assert "dev@sample-site.example.com" not in aliases
    assert "Sample" not in aliases.values()
