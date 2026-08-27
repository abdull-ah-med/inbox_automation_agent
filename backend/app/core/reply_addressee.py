"""Deterministic reply addressee for draft greetings and primary To."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from app.core.email_quotes import split_quoted_history
from app.core.internal_mail import extract_email_address, thread_counterpart

_ANGLE = re.compile(r"^(?P<name>[^<]+)<(?P<email>[^<>@\s]+@[^<>@\s]+)>\s*$")
# Closing / sign-off then a single personal-name line (not a role mailbox label).
_SIGNATURE_NAME = re.compile(
    r"(?is)(?:thanks|thank you|regards|best regards|best|sincerely|cheers|warm regards)"
    r"[,!]?\s*\n+\s*([A-Z][a-zA-Z'" + "\u2019" + r"\-]{1,40})\s*(?:\n|$)"
)
# Zendesk / helpdesk agent byline: "Alex Taylor (SampleHelpdesk)" then a date.
_AGENT_BYLINE = re.compile(
    r"(?m)^([A-Z][a-zA-Z'"
    + "\u2019"
    + r"\-]{1,40})(?:\s+[A-Z][a-zA-Z'"
    + "\u2019"
    + r"\-]{1,40})?\s*\([^)\n]{2,80}\)\s*$"
)
# Bare closing: "Alex Taylor\nCustomer Support" (no Thanks line).
_TITLE_CLOSING = re.compile(
    r"(?im)\n([A-Z][a-zA-Z'"
    + "\u2019"
    + r"\-]{1,40})(?:\s+[A-Z][a-zA-Z'"
    + "\u2019"
    + r"\-]{1,40})?\s*\n+"
    r"(?:customer support|support|help desk|helpdesk|technical support|"
    r"account manager|success manager)\s*(?:\n|$)"
)

# Local-parts that are job/role mailboxes, not personal names.
_ROLE_LOCALS = frozenset(
    {
        "dev",
        "developer",
        "developers",
        "engineering",
        "eng",
        "info",
        "information",
        "support",
        "help",
        "helpdesk",
        "sales",
        "admin",
        "administrator",
        "ops",
        "operations",
        "hr",
        "it",
        "team",
        "office",
        "contact",
        "contacts",
        "service",
        "services",
        "billing",
        "finance",
        "accounting",
        "accounts",
        "marketing",
        "legal",
        "security",
        "noreply",
        "no-reply",
        "donotreply",
        "mail",
        "hello",
        "enquiry",
        "enquiries",
        "inquiry",
        "inquiries",
        "clients",
        "client",
        "customers",
        "customer",
        "partners",
        "partner",
        "vendors",
        "vendor",
    }
)


class _MessageLike(Protocol):
    sender: str | None
    direction: object
    to_recipients: list[str] | None
    body_text: str | None


@dataclass(frozen=True, slots=True)
class ReplyAddressee:
    """Who the draft should salute and primarily address."""

    raw: str
    email: str
    salute_name: str
    source: str  # latest_inbound | last_outbound_to | thread_counterpart


def salute_name_from_party(raw: str) -> str:
    """Best-effort first name / local-part for ``Hi {name},``."""
    text = (raw or "").strip()
    if not text:
        return ""
    angled = _ANGLE.match(text)
    if angled:
        display = angled.group("name").strip().strip("\"'")
        if display:
            first = display.split()[0]
            if not _is_role_token(first):
                return first
        local = angled.group("email").split("@", 1)[0]
        return _local_salute(local)
    addr = extract_email_address(text)
    if addr is not None:
        return _local_salute(addr.split("@", 1)[0])
    return text.split()[0]


def _local_salute(local: str) -> str:
    token = local.strip().split(".", 1)[0].split("_", 1)[0].split("-", 1)[0]
    if not token:
        return local
    if token.isupper() or token.islower():
        return token[:1].upper() + token[1:].lower()
    return token


def _is_role_token(token: str) -> bool:
    compact = token.strip().lower().replace(".", "").replace("-", "").replace("_", "")
    return compact in _ROLE_LOCALS


def _email_local(email: str) -> str:
    return email.split("@", 1)[0].strip().lower()


def is_role_mailbox_salute(*, salute_name: str, email: str) -> bool:
    """True when the salute is just a role mailbox local-part (e.g. Dev@…)."""
    if not salute_name or not email:
        return False
    if not _is_role_token(salute_name):
        return False
    local = _email_local(email)
    local_first = local.split(".", 1)[0].split("_", 1)[0].split("-", 1)[0]
    return salute_name.strip().lower() == local_first or _is_role_token(local)


def _unique_reply_text(body: str | None) -> str:
    """Newest comment only — drop quoted prior comments / reply history."""
    text = (body or "").strip()
    if not text:
        return ""
    main, _quoted = split_quoted_history(text)
    return (main or "").strip()


def _owner_first_name(mailbox_owner: str | None) -> str | None:
    token = (mailbox_owner or "").strip().split()[0] if mailbox_owner else ""
    return token or None


def _usable_person_name(
    name: str | None,
    *,
    reject_names: frozenset[str] | set[str] | None = None,
) -> str | None:
    if not name:
        return None
    cleaned = name.strip()
    if not cleaned or _is_role_token(cleaned):
        return None
    if reject_names and cleaned.casefold() in {n.casefold() for n in reject_names}:
        return None
    return cleaned


def signature_first_name(
    body: str | None,
    *,
    reject_names: frozenset[str] | set[str] | None = None,
) -> str | None:
    """Pull a personal first name from a closing signature in the unique reply."""
    text = _unique_reply_text(body)
    if not text:
        return None
    tail = text[-1200:] if len(text) > 1200 else text
    matches = list(_SIGNATURE_NAME.finditer(tail))
    if not matches:
        return None
    return _usable_person_name(matches[-1].group(1), reject_names=reject_names)


def agent_byline_first_name(
    body: str | None,
    *,
    reject_names: frozenset[str] | set[str] | None = None,
) -> str | None:
    """Zendesk-style ``Name (Org)`` byline on the unique tip comment."""
    text = _unique_reply_text(body)
    if not text:
        return None
    match = _AGENT_BYLINE.search(text)
    if match is None:
        return None
    return _usable_person_name(match.group(1), reject_names=reject_names)


def title_closing_first_name(
    body: str | None,
    *,
    reject_names: frozenset[str] | set[str] | None = None,
) -> str | None:
    """``Alex Taylor\\nCustomer Support`` style closing without a Thanks line."""
    text = _unique_reply_text(body)
    if not text:
        return None
    matches = list(_TITLE_CLOSING.finditer(f"\n{text}"))
    if not matches:
        return None
    return _usable_person_name(matches[-1].group(1), reject_names=reject_names)


def addressee_first_name_from_body(
    body: str | None,
    *,
    reject_names: frozenset[str] | set[str] | None = None,
) -> str | None:
    """Best personal name for a role-mailbox tip body (agent byline > signature)."""
    return (
        agent_byline_first_name(body, reject_names=reject_names)
        or signature_first_name(body, reject_names=reject_names)
        or title_closing_first_name(body, reject_names=reject_names)
    )


def _direction_value(direction: object) -> str:
    if direction is None:
        return ""
    value = getattr(direction, "value", direction)
    return str(value).strip().lower()


def _better_salute_for_email(
    *,
    email: str,
    messages: list[_MessageLike] | tuple[_MessageLike, ...] | None,
    reject_names: frozenset[str] | set[str] | None = None,
) -> str | None:
    """Prefer a human display/signature name for the same address elsewhere in-thread."""
    if not messages:
        return None
    target = email.strip().lower()
    # Newest first: latest signature / display for this person wins.
    for msg in reversed(list(messages)):
        sender = getattr(msg, "sender", None) or ""
        sender_email = extract_email_address(sender)
        if sender_email is None or sender_email.lower() != target:
            continue
        from_party = salute_name_from_party(sender)
        if (
            from_party
            and not is_role_mailbox_salute(salute_name=from_party, email=target)
            and _usable_person_name(from_party, reject_names=reject_names)
        ):
            return from_party
        signed = addressee_first_name_from_body(
            getattr(msg, "body_text", None),
            reject_names=reject_names,
        )
        if signed:
            return signed
    # Also check outbound tip body when Elise already used a short name? Skip —
    # "Thanks Div." is ambiguous without more parsing.
    return None


def resolve_reply_addressee(
    *,
    mailbox: str | None,
    messages: list[_MessageLike] | tuple[_MessageLike, ...] | None,
    mailbox_owner: str | None = None,
) -> ReplyAddressee | None:
    """Pick who to salute from the tip of the thread, not the thread opener.

    Walk newest → oldest. For each message, use ``thread_counterpart`` (inbound
    sender, else outbound To). First hit wins — so an outbound tip to Dev beats
    an earlier inbound from Smit.

    When the tip address is a role mailbox (``Dev@``, ``support@``, …), enrich
    the salute from an in-thread display name or signature for that same email
    (e.g. ``Thanks,\\nDivyansh``) instead of greeting the role label.

    Never salute the mailbox owner — that person signs the outbound reply.
    """
    if not messages:
        return None
    message_list = list(messages)
    owner = _owner_first_name(mailbox_owner)
    reject = frozenset({owner}) if owner else frozenset()
    for msg in reversed(message_list):
        direction = _direction_value(msg.direction)
        raw = thread_counterpart(
            mailbox=mailbox,
            sender=msg.sender,
            direction=direction,
            to_recipients=list(msg.to_recipients or []),
        )
        if not raw:
            continue
        email = extract_email_address(raw)
        if email is None:
            continue
        if direction == "inbound":
            source = "latest_inbound"
        elif direction == "outbound":
            source = "last_outbound_to"
        else:
            source = "thread_counterpart"
        salute = salute_name_from_party(raw)
        if is_role_mailbox_salute(salute_name=salute, email=email):
            tip_signed = None
            # Only the inbound tip's body belongs to the addressee.
            if direction == "inbound":
                tip_signed = addressee_first_name_from_body(
                    getattr(msg, "body_text", None),
                    reject_names=reject,
                )
            better = tip_signed or _better_salute_for_email(
                email=email,
                messages=message_list,
                reject_names=reject,
            )
            if better:
                salute = better
        elif owner and salute.casefold() == owner.casefold():
            # Display name accidentally matched the owner; try body enrichment.
            better = addressee_first_name_from_body(
                getattr(msg, "body_text", None) if direction == "inbound" else None,
                reject_names=reject,
            ) or _better_salute_for_email(
                email=email,
                messages=message_list,
                reject_names=reject,
            )
            if better:
                salute = better
        return ReplyAddressee(
            raw=raw.strip(),
            email=email,
            salute_name=salute,
            source=source,
        )
    return None


def format_reply_addressee_block(addressee: ReplyAddressee) -> str:
    """Trusted draft-prompt block: hard constraint for salutation + primary To."""
    return (
        "Reply addressee (hard constraint):\n"
        f"- Salute: {addressee.salute_name}\n"
        f"- Primary To: {addressee.email}\n"
        f"- Source: {addressee.source}\n"
        f"- Full party: {addressee.raw}\n"
        "The reply_body greeting must address Salute (e.g. "
        f'"Hi {addressee.salute_name},"). Do not greet the thread opener '
        "unless they are this addressee. Do not salute a mailbox role label "
        "(Dev, Support, Info, …) when Salute is a personal name. Primary "
        "suggested_recipients must match Primary To when a single recipient "
        "is appropriate.\n"
    )
