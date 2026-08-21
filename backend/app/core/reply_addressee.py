"""Deterministic reply addressee for draft greetings and primary To."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from app.core.internal_mail import extract_email_address, thread_counterpart

_ANGLE = re.compile(r"^(?P<name>[^<]+)<(?P<email>[^<>@\s]+@[^<>@\s]+)>\s*$")


class _MessageLike(Protocol):
    sender: str | None
    direction: object
    to_recipients: list[str] | None


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
            return display.split()[0]
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


def _direction_value(direction: object) -> str:
    if direction is None:
        return ""
    value = getattr(direction, "value", direction)
    return str(value).strip().lower()


def resolve_reply_addressee(
    *,
    mailbox: str | None,
    messages: list[_MessageLike] | tuple[_MessageLike, ...] | None,
) -> ReplyAddressee | None:
    """Pick who to salute from the tip of the thread, not the thread opener.

    Walk newest → oldest. For each message, use ``thread_counterpart`` (inbound
    sender, else outbound To). First hit wins — so an outbound tip to Dev beats
    an earlier inbound from Smit.
    """
    if not messages:
        return None
    for msg in reversed(list(messages)):
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
        return ReplyAddressee(
            raw=raw.strip(),
            email=email,
            salute_name=salute_name_from_party(raw),
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
        "unless they are this addressee. Primary suggested_recipients must "
        "match Primary To when a single recipient is appropriate.\n"
    )
