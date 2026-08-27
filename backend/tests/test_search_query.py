"""Discord-style search filters: from, contains, subject, direction, mailbox."""

from app.services.search_query import parse_search_query


def test_parse_stacks_from_subject_mailbox_and_free_text() -> None:
    parsed = parse_search_query("from:vendor@example.com subject:packet mailbox:sales overdue")
    assert parsed.senders == ("vendor@example.com",)
    assert parsed.subjects == ("packet",)
    assert parsed.mailboxes == ("sales",)
    assert parsed.free_text == "overdue"


def test_parse_quoted_from_and_contains() -> None:
    parsed = parse_search_query('from:"Elise Kelvin" contains:"drug screen" invoice')
    assert parsed.senders == ("Elise Kelvin",)
    assert parsed.contains == ("drug screen",)
    assert parsed.free_text == "invoice"


def test_parse_direction_aliases() -> None:
    inbound = parse_search_query("direction:inbound")
    outgoing = parse_search_query("direction:sent packet")
    assert inbound.directions == ("inbound",)
    assert inbound.free_text == ""
    assert outgoing.directions == ("outbound",)
    assert outgoing.free_text == "packet"


def test_parse_repeated_from_is_or_candidates() -> None:
    parsed = parse_search_query("from:vendor@example.com from:client@example.com")
    assert parsed.senders == ("vendor@example.com", "client@example.com")
    assert parsed.free_text == ""


def test_parse_unknown_key_stays_in_free_text() -> None:
    parsed = parse_search_query("foo:bar packet")
    assert parsed.senders == ()
    assert parsed.free_text == "foo:bar packet"


def test_parse_strips_html_before_reading_filters() -> None:
    parsed = parse_search_query("<b>from:vendor@example.com</b> packet")
    assert parsed.senders == ("vendor@example.com",)
    assert parsed.free_text == "packet"


def test_parse_from_allows_space_after_colon() -> None:
    parsed = parse_search_query("from: vendor@example.com packet")
    assert parsed.senders == ("vendor@example.com",)
    assert parsed.free_text == "packet"


def test_parse_dangling_from_is_empty_not_a_keyword() -> None:
    parsed = parse_search_query("from:")
    assert parsed.senders == ()
    assert parsed.free_text == ""
    assert parsed.is_empty()


def test_parse_dangling_from_does_not_swallow_the_next_operator() -> None:
    parsed = parse_search_query("from: subject:packet")
    assert parsed.senders == ()
    assert parsed.subjects == ("packet",)
    assert parsed.free_text == ""
