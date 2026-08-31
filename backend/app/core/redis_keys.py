"""Central Redis key patterns for dedup, subscriptions, poll cursors, MSAL cache, and Slack."""

from __future__ import annotations

DEDUP_TTL_SECONDS = 86_400
DEDUP_PROCESSING_TTL_SECONDS = 15 * 60
DEDUP_VALUE_PROCESSING = "processing"
DEDUP_VALUE_COMPLETED = "completed"

# Separate from ingest dedup so webhook/poll retries cannot overlap Haiku calls.
TRIAGE_LOCK_TTL_SECONDS = 15 * 60

SUBSCRIPTION_RENEW_BEFORE_SECONDS = 12 * 3600  # renew if <12h left
DEFAULT_POLL_LOOKBACK_SECONDS = 15 * 60
MISSED_POLL_LOOKBACK_SECONDS = 2 * 3600

# Window while create_subscription awaits Graph's validationToken POST.
VALIDATION_PENDING_TTL_SECONDS = 120

MSAL_TOKEN_CACHE_KEY = "msal:token_cache"
MSAL_TOKEN_CACHE_LOCK_KEY = "msal:token_cache:lock"
MSAL_TOKEN_CACHE_LOCK_TTL_SECONDS = 30
# Bound shared MSAL blob lifetime so abandoned cache entries expire.
MSAL_TOKEN_CACHE_TTL_SECONDS = 7 * 24 * 3600  # 604800

SCHEDULER_POLL_LOCK_KEY = "scheduler:lock:poll"
SCHEDULER_RENEW_LOCK_KEY = "scheduler:lock:renew"
SCHEDULER_RECONCILE_LOCK_KEY = "scheduler:lock:reconcile"

# Owner-token lock TTLs. Work must finish (or release) before expiry.
# https://redis.io/docs/latest/develop/clients/patterns/distributed-locks/
RECONCILE_LOCK_TTL_SECONDS = 600

WEBHOOK_VALIDATION_PENDING_KEY = "graph:webhook:validation_pending"

# Durable Graph webhook offload (Redis Streams). Survives process restart;
# BackgroundTasks does not.
GRAPH_WEBHOOK_STREAM_KEY = "graph:webhook:stream"
GRAPH_WEBHOOK_CONSUMER_GROUP = "webhook-processors"
GRAPH_WEBHOOK_STREAM_MAXLEN = 10_000
GRAPH_WEBHOOK_CLAIM_MIN_IDLE_MS = 60_000


def _normalize_folder(folder: str) -> str:
    return folder.strip().lower().replace(" ", "")


def dedup_key(mailbox: str, message_id: str) -> str:
    return f"dedup:{mailbox}:{message_id}"


def triage_lock_key(mailbox: str, message_id: str) -> str:
    return f"triage:lock:{mailbox}:{message_id}"


def inbox_subscription_key(mailbox: str) -> str:
    return f"graph:sub:inbox:{mailbox}"


def sent_items_subscription_key(mailbox: str) -> str:
    return f"graph:sub:sentitems:{mailbox}"


def subscription_key(mailbox: str, folder: str = "inbox") -> str:
    """Redis key for a mailbox folder subscription.

    ``folder="inbox"`` uses the new inbox key. Legacy callers that omit
    ``folder`` still resolve to inbox. The pre-folder key ``graph:sub:{mailbox}``
    is read as a one-shot fallback in ``subscription_service``.
    """
    normalized = _normalize_folder(folder)
    if normalized == "sentitems":
        return sent_items_subscription_key(mailbox)
    return inbox_subscription_key(mailbox)


def legacy_subscription_key(mailbox: str) -> str:
    """Pre-folder Redis key used only for one-shot migration reads."""
    return f"graph:sub:{mailbox}"


def poll_cursor_key(mailbox: str, folder: str = "inbox") -> str:
    """Watermark for the interval poller.

    Inbound folders (inbox / junkemail) share ``graph:poll:last_checked:{mailbox}``.
    Sent Items uses a separate key so outbound catch-up cannot move the inbound
    watermark (and vice versa).
    """
    normalized = _normalize_folder(folder)
    if normalized == "sentitems":
        return f"graph:poll:last_checked:sentitems:{mailbox}"
    return f"graph:poll:last_checked:{mailbox}"


def webhook_rate_limit_key(client_ip: str) -> str:
    return f"ratelimit:webhook:{client_ip}"


def refresh_grace_key(token_hash: str) -> str:
    """Short-lived cache of the newly issued refresh pair after rotation."""
    return f"auth:refresh_grace:{token_hash}"


# Idempotent Slack review-card posts (one card per mailbox+message).
SLACK_POSTED_TTL_SECONDS = 86_400


def slack_posted_key(mailbox: str, message_id: str) -> str:
    return f"slack:posted:{mailbox}:{message_id}"
