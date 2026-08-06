"""Central Redis key patterns for dedup, subscriptions, poll cursors, MSAL cache, and Slack."""

from __future__ import annotations

DEDUP_TTL_SECONDS = 86_400
DEDUP_PROCESSING_TTL_SECONDS = 15 * 60
DEDUP_VALUE_PROCESSING = "processing"
DEDUP_VALUE_COMPLETED = "completed"

# Separate from ingest dedup so webhook/poll retries cannot overlap Haiku calls.
TRIAGE_LOCK_TTL_SECONDS = 15 * 60

POLL_INTERVAL_SECONDS = 300
SUBSCRIPTION_RENEW_BEFORE_SECONDS = 12 * 3600  # renew if <12h left
DEFAULT_POLL_LOOKBACK_SECONDS = 15 * 60
MISSED_POLL_LOOKBACK_SECONDS = 2 * 3600

# Window while create_subscription awaits Graph's validationToken POST.
VALIDATION_PENDING_TTL_SECONDS = 120

MSAL_TOKEN_CACHE_KEY = "msal:token_cache"
MSAL_TOKEN_CACHE_LOCK_KEY = "msal:token_cache:lock"
MSAL_TOKEN_CACHE_LOCK_TTL_SECONDS = 30

SCHEDULER_POLL_LOCK_KEY = "scheduler:lock:poll"
SCHEDULER_RENEW_LOCK_KEY = "scheduler:lock:renew"
SCHEDULER_RECONCILE_LOCK_KEY = "scheduler:lock:reconcile"

# Owner-token lock TTLs. Work must finish (or release) before expiry.
# https://redis.io/docs/latest/develop/clients/patterns/distributed-locks/
RECONCILE_LOCK_TTL_SECONDS = 600

WEBHOOK_VALIDATION_PENDING_KEY = "graph:webhook:validation_pending"


def dedup_key(mailbox: str, message_id: str) -> str:
    return f"dedup:{mailbox}:{message_id}"


def triage_lock_key(mailbox: str, message_id: str) -> str:
    return f"triage:lock:{mailbox}:{message_id}"


def subscription_key(mailbox: str) -> str:
    return f"graph:sub:{mailbox}"


def poll_cursor_key(mailbox: str) -> str:
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
