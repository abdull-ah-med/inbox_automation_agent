"""Deterministic reply addressee for draft greetings and primary To."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from app.core.automated_mail import AUTOMATED_LOCAL_COMPACTS
from app.core.email_quotes import split_quoted_history
from app.core.internal_mail import extract_email_address, thread_counterpart
from app.core.person_name import (
    display_name_first_name,
    line_looks_like_company_signoff,
    looks_like_person_first_name,
    looks_like_surname,
)
from app.llm._vendor.talon.signature import bruteforce as talon_bruteforce

# Closing / sign-off then a single personal-name line.
_SIGNATURE_NAME = re.compile(
    r"(?is)(?:thanks|thank you|regards|best regards|best wishes|best|sincerely|cheers|warm regards)"
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
# Bare closing: "Alex Taylor\nCustomer Support" or "Cydney Blumenthal\nDirector of …"
# Title vocabulary follows common business-signature patterns (Director/VP/Manager/…).
_TITLE_CLOSING = re.compile(
    r"(?im)\n([A-Z][a-zA-Z'"
    + "\u2019"
    + r"\-]{1,40})(?:\s+[A-Z][a-zA-Z'"
    + "\u2019"
    + r"\-]{1,40})?\s*\n+"
    r"(?:customer support|support|help desk|helpdesk|technical support|"
    r"account manager|success manager|"
    r"director(?:\s+of\s+[^\n]{2,80})?|"
    r"vice president(?:\s+of\s+[^\n]{2,80})?|"
    r"vp(?:\s+of\s+[^\n]{2,80})?|"
    r"manager(?:\s+of\s+[^\n]{2,80})?|"
    r"head of [^\n]{2,80}|"
    r"president|ceo|cto|cfo|coo|founder|co-founder|"
    r"engineer|analyst|consultant|specialist|coordinator|"
    r"administrator|executive)\s*(?:\n|$)"
)
# Zendesk ticket chrome with no named agent on the newest comment.
_ZENDESK_TICKET_CHROME = re.compile(
    r"(?is)(?:##-\s*please type your reply above this line\s*-##|"
    r"your request\s*\(\d+\)\s*has been (?:received|updated))"
)
_MOBILE_SIGNATURE_STUB = re.compile(
    r"(?i)^sent\s+from\s+(?:my\s+)?(?:iphone|ipad|android|mobile|blackberry|mailbox)",
)


class _MessageLike(Protocol):
    sender: str | None
    direction: object
    to_recipients: list[str] | None
    body_text: str | None
    sender_display_name: str | None
    meeting_message_type: str | None


@dataclass(frozen=True, slots=True)
class ReplyAddressee:
    """Who the draft should salute and primarily address."""

    raw: str
    email: str
    salute_name: str
    source: str  # latest_inbound | last_outbound_to | thread_counterpart
    directory_hit: bool = False
    # signature | byline | title_close | talon_signature | display | directory | none
    source_kind: str = "none"


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
    if not looks_like_person_first_name(cleaned):
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
    match = matches[-1]
    line_start = tail.rfind("\n", 0, match.start(1))
    line = tail[line_start + 1 :] if line_start >= 0 else tail[match.start(1) :]
    if line_looks_like_company_signoff(line):
        return None
    return _usable_person_name(match.group(1), reject_names=reject_names)


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


def talon_signature_first_name(
    body: str | None,
    *,
    reject_names: frozenset[str] | set[str] | None = None,
) -> str | None:
    """First validated name from Talon-isolated signature block."""
    text = _unique_reply_text(body)
    if not text:
        return None
    _stripped, signature = talon_bruteforce.extract_signature(text)
    if not signature:
        return None
    for line in signature.splitlines():
        stripped = line.strip()
        if not stripped or _MOBILE_SIGNATURE_STUB.match(stripped):
            continue
        if stripped.startswith("--"):
            continue
        if re.match(r"(?i)^(thanks|thank you|regards|best|sincerely|cheers)", stripped):
            continue
        if line_looks_like_company_signoff(stripped):
            continue
        tokens = stripped.split()
        if (
            len(tokens) >= 2
            and looks_like_person_first_name(tokens[0])
            and looks_like_surname(tokens[1])
        ):
            usable = _usable_person_name(tokens[0], reject_names=reject_names)
            if usable:
                return usable
        token = tokens[0] if tokens else None
        usable = _usable_person_name(token, reject_names=reject_names)
        if usable:
            return usable
    return None


def addressee_first_name_from_body(
    body: str | None,
    *,
    reject_names: frozenset[str] | set[str] | None = None,
    allow_signature: bool = True,
) -> tuple[str | None, str | None]:
    """Best personal name for a tip body (agent byline > signature > title closing).

    Returns ``(name, source_kind)`` where source_kind is byline / signature /
    title_close, or ``(None, None)`` when no person name is found.
    """
    byline = agent_byline_first_name(body, reject_names=reject_names)
    if byline:
        return byline, "byline"
    if allow_signature:
        signed = signature_first_name(body, reject_names=reject_names)
        if signed:
            return signed, "signature"
    titled = title_closing_first_name(body, reject_names=reject_names)
    if titled:
        return titled, "title_close"
    if allow_signature:
        talon_signed = talon_signature_first_name(body, reject_names=reject_names)
        if talon_signed:
            return talon_signed, "talon_signature"
    return None, None


def _is_zendesk_ticket_chrome(body: str | None) -> bool:
    """True when the unique tip looks like Zendesk ticket mail, not a person."""
    text = _unique_reply_text(body)
    if not text:
        return False
    return _ZENDESK_TICKET_CHROME.search(text) is not None


def _tip_person_name(
    body: str | None,
    *,
    reject_names: frozenset[str] | set[str] | None = None,
) -> tuple[str | None, str | None]:
    """Personal name from the tip body; Zendesk chrome ignores leaked sign-offs."""
    if _is_zendesk_ticket_chrome(body):
        # Auto-acks and updates often embed our prior Thanks,Owner. Only trust
        # agent byline / support-title closing — never signature_first_name.
        return addressee_first_name_from_body(
            body,
            reject_names=reject_names,
            allow_signature=False,
        )
    return addressee_first_name_from_body(body, reject_names=reject_names)


def _normalize_directory(directory: Mapping[str, str] | None) -> dict[str, str]:
    if not directory:
        return {}
    return {
        key.strip().lower(): value.strip()
        for key, value in directory.items()
        if key and value and str(value).strip()
    }


def _direction_value(direction: object) -> str:
    if direction is None:
        return ""
    value = getattr(direction, "value", direction)
    return str(value).strip().lower()


def _sender_local_compact(sender: str | None) -> str | None:
    address = extract_email_address(sender)
    if address is None or "@" not in address:
        return None
    local = address.partition("@")[0].strip().lower()
    return local.replace(".", "").replace("-", "").replace("_", "") or None


def _display_salute_from_message(msg: _MessageLike) -> str | None:
    """Gated Graph From display name — last resort after body extraction."""
    meeting = getattr(msg, "meeting_message_type", None)
    if meeting and str(meeting).strip():
        return None
    compact = _sender_local_compact(getattr(msg, "sender", None))
    if compact and compact in AUTOMATED_LOCAL_COMPACTS:
        return None
    display = getattr(msg, "sender_display_name", None)
    return display_name_first_name(display if isinstance(display, str) else None)


def _better_salute_for_email(
    *,
    email: str,
    messages: list[_MessageLike] | tuple[_MessageLike, ...] | None,
    reject_names: frozenset[str] | set[str] | None = None,
) -> tuple[str | None, str | None]:
    """Prefer a person signature/byline for the same address elsewhere in-thread."""
    if not messages:
        return None, None
    target = email.strip().lower()
    for msg in reversed(list(messages)):
        sender = getattr(msg, "sender", None) or ""
        sender_email = extract_email_address(sender)
        if sender_email is None or sender_email.lower() != target:
            continue
        signed, kind = _tip_person_name(
            getattr(msg, "body_text", None),
            reject_names=reject_names,
        )
        if signed:
            return signed, kind
    return None, None


def resolve_reply_addressee(
    *,
    mailbox: str | None,
    messages: list[_MessageLike] | tuple[_MessageLike, ...] | None,
    mailbox_owner: str | None = None,
    directory: Mapping[str, str] | None = None,
    suppress_local_part: bool = True,
) -> ReplyAddressee | None:
    """Pick who to salute from the tip of the thread, not the thread opener.

    Walk newest → oldest. For each message, use ``thread_counterpart`` (inbound
    sender, else outbound To). First hit wins.

    Salute priority:
    1. Contacts directory (taught alias)
    2. Confident person name from the letter's sign / agent byline / title close
    3. Gated Graph From display (person-shaped, not calendar/noreply)
    4. Empty ``salute_name`` → bare ``Hi,`` / ``Hello,``

    Never the email local-part, a role mailbox label, a company brand, or the
    mailbox owner (that person signs the outbound reply).
    """
    _ = suppress_local_part
    if not messages:
        return None
    message_list = list(messages)
    owner = _owner_first_name(mailbox_owner)
    reject = frozenset({owner}) if owner else frozenset()
    directory_map = _normalize_directory(directory)
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

        email_key = email.strip().lower()
        directory_name = directory_map.get(email_key)
        if directory_name:
            salute = directory_name
            if owner and salute.casefold() == owner.casefold():
                salute = ""
                source_kind = "none"
                directory_hit = False
            else:
                source_kind = "directory"
                directory_hit = True
            return ReplyAddressee(
                raw=raw.strip(),
                email=email,
                salute_name=salute,
                source=source,
                directory_hit=directory_hit,
                source_kind=source_kind,
            )

        salute = ""
        source_kind = "none"
        if direction == "inbound":
            tip_signed, tip_kind = _tip_person_name(
                getattr(msg, "body_text", None),
                reject_names=reject,
            )
            if tip_signed:
                salute = tip_signed
                source_kind = tip_kind or "signature"
        if not salute:
            better, better_kind = _better_salute_for_email(
                email=email,
                messages=message_list,
                reject_names=reject,
            )
            if better:
                salute = better
                source_kind = better_kind or "signature"
        if not salute and direction == "inbound":
            display_salute = _display_salute_from_message(msg)
            if display_salute:
                salute = display_salute
                source_kind = "display"
        if owner and salute and salute.casefold() == owner.casefold():
            salute = ""
            source_kind = "none"
        if salute and not looks_like_person_first_name(salute):
            salute = ""
            source_kind = "none"
        if not salute:
            source_kind = "none"
        return ReplyAddressee(
            raw=raw.strip(),
            email=email,
            salute_name=salute,
            source=source,
            directory_hit=False,
            source_kind=source_kind,
        )
    return None


def format_reply_addressee_block(addressee: ReplyAddressee) -> str:
    """Trusted draft-prompt block: hard constraint for salutation + primary To."""
    if not addressee.salute_name:
        return (
            "Reply addressee (hard constraint):\n"
            "- Salute: (none — no personal name known)\n"
            f"- Primary To: {addressee.email}\n"
            f"- Source: {addressee.source}\n"
            f"- Full party: {addressee.raw}\n"
            'The reply_body greeting must open with "Hi," (bare, no name) — do NOT '
            "invent a name from the email address, do NOT use the local-part, do NOT "
            'guess. Tone profile still governs formality (e.g. "Hello,"). Primary '
            "suggested_recipients must match Primary To when a single recipient "
            "is appropriate.\n"
        )
    return (
        "Reply addressee (hard constraint):\n"
        f"- Salute: {addressee.salute_name}\n"
        f"- Primary To: {addressee.email}\n"
        f"- Source: {addressee.source}\n"
        f"- Full party: {addressee.raw}\n"
        "The reply_body greeting must address Salute (e.g. "
        f'"Hi {addressee.salute_name},"). Do not greet the thread opener '
        "unless they are this addressee. Do not invent a name from the email "
        "address or local-part. Primary "
        "suggested_recipients must match Primary To when a single recipient "
        "is appropriate.\n"
    )
