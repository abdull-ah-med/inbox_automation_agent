"""Person-shaped display names — shared gate for automation and salutations."""

from __future__ import annotations

import re

_ORG_SECOND_TOKENS = frozenset(
    {
        "newsletter",
        "newsletters",
        "subscriptions",
        "subscription",
        "notifications",
        "notification",
        "support",
        "team",
        "office",
        "department",
        "helpdesk",
        "noreply",
        "bounce",
        "mailer",
        "services",
        "safety",
        "developer",
        "manager",
        "engineer",
        "specialist",
        "director",
        "analyst",
        "intern",
        "associate",
        "coordinator",
        "lead",
        "president",
        "consultant",
        "administrator",
        "architect",
        "officer",
        "of",
        "for",
        "the",
        "and",
    }
)
_NON_PERSON_FIRST_TOKENS = frozenset(
    {
        "digital",
        "vendor",
        "customer",
        "newsletter",
        "accounts",
        "billing",
        "marketing",
        "office",
        "support",
        "team",
        "delve",
    }
)
_LAST_FIRST = re.compile(r"^[A-Za-z][A-Za-z'\-]+,\s+[A-Z][A-Za-z]")
_NAME_WITH_BRAND = re.compile(
    r"^([A-Za-z][A-Za-z'\-]+(?:\s+[A-Za-z][A-Za-z'\-.]+){1,3})\s*\([^)]+\)\s*$"
)


def tokens_look_like_person(name: str) -> bool:
    tokens = [part for part in name.split() if part]
    if len(tokens) < 2:
        return False
    first, second = tokens[0], tokens[1]
    if first.isupper() and len(first) > 1:
        return False
    if first.lower() in _ORG_SECOND_TOKENS or second.lower() in _ORG_SECOND_TOKENS:
        return False
    return first[0].isalpha() and first[0].isupper() and second[0].isalpha() and second[0].isupper()


def person_shaped_from(display_name: str | None, sender: str | None = None) -> bool:
    """True for 'Alex Taylor (SampleHelpdesk)' or Exchange 'Hooker, Ruth E'."""
    _ = sender
    name = (display_name or "").strip().strip("\"'")
    if not name:
        return False
    if _LAST_FIRST.match(name):
        return True
    branded = _NAME_WITH_BRAND.match(name)
    if branded is not None:
        return tokens_look_like_person(branded.group(1))
    return tokens_look_like_person(name)


def looks_like_person_first_name(token: str) -> bool:
    """True for 'Alex' / 'Cydney'. False for brands ('SampleHelpdesk') and locals."""
    cleaned = (token or "").strip()
    if len(cleaned) < 2 or len(cleaned) > 40:
        return False
    if not cleaned[0].isalpha() or not cleaned[0].isupper():
        return False
    if any(ch.isupper() for ch in cleaned[1:]):
        return False
    letters = cleaned.replace("-", "").replace("'", "").replace("\u2019", "")
    return letters.isalpha()


def looks_like_surname(token: str) -> bool:
    cleaned = (token or "").strip()
    if len(cleaned) < 2 or len(cleaned) > 40:
        return False
    if not cleaned[0].isupper():
        return False
    letters = cleaned.replace("-", "").replace("'", "").replace("\u2019", "")
    return letters.isalpha() and not any(ch.isupper() for ch in cleaned[1:])


def _given_name_from_display(display: str) -> str | None:
    """Extract a first-name token from a person-shaped display string."""
    name = display.strip().strip("\"'")
    if not name:
        return None
    if "," in name:
        given_part = name.split(",", 1)[1].strip()
        given_tokens = given_part.split()
        return given_tokens[0] if given_tokens else None
    branded = _NAME_WITH_BRAND.match(name)
    if branded is not None:
        person_segment = branded.group(1)
        segment_tokens = person_segment.split() if person_segment else []
        return segment_tokens[0] if segment_tokens else None
    name_tokens = name.split()
    return name_tokens[0] if name_tokens else None


def line_looks_like_company_signoff(line: str) -> bool:
    tokens = [part for part in line.strip().split() if part]
    if len(tokens) < 2:
        return False
    first, second = tokens[0], tokens[1]
    if first.lower() in _NON_PERSON_FIRST_TOKENS:
        return True
    if first.lower() in _ORG_SECOND_TOKENS or second.lower() in _ORG_SECOND_TOKENS:
        return True
    if looks_like_person_first_name(first) and looks_like_surname(second):
        return False
    return second[0].isupper()


def display_name_first_name(display: str | None) -> str | None:
    """Gated first name from Graph From display — empty when not person-shaped."""
    if not person_shaped_from(display):
        return None
    token = _given_name_from_display(display or "")
    if not token or not looks_like_person_first_name(token):
        return None
    return token
