"""Scope key helpers for the feedback-loops scope ladder.

Lives in ``app.core`` so repositories can import it without depending on services.

The scope ladder (narrowest to widest):
  thread -> sender_address -> sender_domain ->
  mailbox+routing_category -> mailbox -> global
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

_VALID_SCOPES = frozenset(
    {
        "thread",
        "sender_address",
        "sender_domain",
        "mailbox+routing_category",
        "mailbox",
        "global",
    }
)

# Auto-widen never jumps to global.
_WIDEN_LADDER = (
    "thread",
    "sender_address",
    "sender_domain",
    "mailbox+routing_category",
    "mailbox",
)


def scope_key_for(
    scope: str,
    *,
    thread_id: object = None,
    sender_address: str | None = None,
    sender_domain: str | None = None,
    mailbox: str | None = None,
    routing_category: str | None = None,
) -> str:
    """Return the canonical scope_key string for the given scope level.

    Examples
    --------
    scope_key_for("thread", thread_id="abc-123")                     -> "thread:abc-123"
    scope_key_for("sender_address", sender_address="x@y")            -> "sender:x@y"
    scope_key_for("sender_domain", sender_domain="y.com")            -> "domain:y.com"
    scope_key_for("mailbox+routing_category", mailbox="e@x",
                  routing_category="billing")                        -> "mailbox:e@x:billing"
    scope_key_for("mailbox", mailbox="e@x")                          -> "mailbox:e@x"
    scope_key_for("global")                                          -> "global:"
    """
    if scope not in _VALID_SCOPES:
        raise ValueError(f"Unknown scope {scope!r}. Valid: {sorted(_VALID_SCOPES)}")

    if scope == "thread":
        if thread_id is None:
            raise ValueError("thread_id is required for scope='thread'")
        return f"thread:{thread_id}"

    if scope == "sender_address":
        if not sender_address:
            raise ValueError("sender_address is required for scope='sender_address'")
        return f"sender:{sender_address.lower()}"

    if scope == "sender_domain":
        if not sender_domain:
            raise ValueError("sender_domain is required for scope='sender_domain'")
        return f"domain:{sender_domain.lower()}"

    if scope == "mailbox+routing_category":
        if not mailbox:
            raise ValueError("mailbox is required for scope='mailbox+routing_category'")
        if not routing_category:
            raise ValueError("routing_category is required for scope='mailbox+routing_category'")
        return f"mailbox:{mailbox}:{routing_category}"

    if scope == "mailbox":
        if not mailbox:
            raise ValueError("mailbox is required for scope='mailbox'")
        return f"mailbox:{mailbox}"

    return "global:"


def fields_from_scope_key(scope_key: str) -> dict[str, str]:
    """Parse a canonical scope_key into the fields needed to recompute a wider key."""
    key = (scope_key or "").strip()
    if key.startswith("thread:"):
        return {"thread_id": key[7:]}
    if key.startswith("sender:"):
        addr = key[7:].lower()
        fields = {"sender_address": addr}
        if "@" in addr:
            fields["sender_domain"] = addr.rsplit("@", 1)[-1]
        return fields
    if key.startswith("domain:"):
        return {"sender_domain": key[7:].lower()}
    if key.startswith("mailbox:"):
        rest = key[8:]
        if ":" in rest:
            mailbox, category = rest.split(":", 1)
            return {"mailbox": mailbox, "routing_category": category}
        return {"mailbox": rest}
    return {}


def _nonempty_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def resolve_widening_scope(
    requested_scope: str,
    *,
    mailbox: str,
    source_scope_key: str,
    payload: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Return ``(scope, scope_key)`` that retrieval can actually match.

    If the requested ladder step cannot be keyed from the source (e.g. no
    routing_category for mailbox+routing_category), step toward mailbox.
    Never invents ``global`` unless it was requested.
    """
    if requested_scope == "global":
        return "global", scope_key_for("global")

    extra = payload or {}
    parsed = fields_from_scope_key(source_scope_key)
    sender_address = _nonempty_str(extra.get("sender_address")) or parsed.get("sender_address")
    sender_domain = _nonempty_str(extra.get("sender_domain")) or parsed.get("sender_domain")
    routing_category = _nonempty_str(extra.get("routing_category")) or parsed.get(
        "routing_category"
    )
    thread_id = extra.get("thread_id") or parsed.get("thread_id")
    mb = _nonempty_str(extra.get("mailbox")) or parsed.get("mailbox") or mailbox

    try:
        start = _WIDEN_LADDER.index(requested_scope)
    except ValueError:
        start = _WIDEN_LADDER.index("mailbox")

    for scope in _WIDEN_LADDER[start:]:
        try:
            key = scope_key_for(
                scope,
                thread_id=thread_id,
                sender_address=sender_address,
                sender_domain=sender_domain,
                mailbox=mb,
                routing_category=routing_category,
            )
            return scope, key
        except ValueError:
            continue
    return "mailbox", scope_key_for("mailbox", mailbox=mb)


_EXPIRY_DAYS: dict[str, int | None] = {
    "thread": 30,
    "sender_address": 90,
    "sender_domain": 90,
    "mailbox+routing_category": 180,
    "mailbox": None,
    "global": None,
}


def expires_at_for_scope(
    scope: str,
    *,
    now: datetime | None = None,
    thread_resolved_at: datetime | None = None,
) -> datetime | None:
    """Return the auto-decay timestamp for *scope*, or None when it does not expire.

    Plan §4.8: thread 30d after RESOLVED, sender_* 90d, mailbox+category 180d,
    mailbox/global never.
    """
    if scope == "thread":
        if thread_resolved_at is None:
            return None
        resolved = thread_resolved_at
        if resolved.tzinfo is None:
            resolved = resolved.replace(tzinfo=UTC)
        return resolved + timedelta(days=30)
    days = _EXPIRY_DAYS.get(scope)
    if days is None:
        return None
    clock = now if now is not None else datetime.now(UTC)
    return clock + timedelta(days=days)
