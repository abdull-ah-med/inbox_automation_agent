"""Central Redis key patterns for dedup, subscriptions, poll cursors, and MSAL cache."""

from __future__ import annotations

# Completed ingest idempotency window (24h).
DEDUP_TTL_SECONDS = 86_400
# In-flight claim TTL — expires so a crashed worker does not block retries forever.
DEDUP_PROCESSING_TTL_SECONDS = 15 * 60
DEDUP_VALUE_PROCESSING = "processing"
DEDUP_VALUE_COMPLETED = "completed"

POLL_INTERVAL_SECONDS = 300
SUBSCRIPTION_RENEW_BEFORE_SECONDS = 12 * 3600  # renew if <12h left
DEFAULT_POLL_LOOKBACK_SECONDS = 15 * 60
MISSED_POLL_LOOKBACK_SECONDS = 2 * 3600

MSAL_TOKEN_CACHE_KEY = "msal:token_cache"


def dedup_key(mailbox: str, message_id: str) -> str:
    return f"dedup:{mailbox}:{message_id}"


def subscription_key(mailbox: str) -> str:
    return f"graph:sub:{mailbox}"


def poll_cursor_key(mailbox: str) -> str:
    return f"graph:poll:last_checked:{mailbox}"
