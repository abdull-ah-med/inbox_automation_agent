"""Central structlog configuration with automatic PII redaction.

Data-privacy policy: we NEVER emit raw email bodies, sender addresses, thread
context, or raw LLM completions to logs. This module is the mechanical backstop
for that policy: even if a developer accidentally binds a sensitive value to a
log event, the redaction processor masks it before the log line is rendered. Log
*metadata* (message_id, thread_id, token_count,
classification_confidence, latency) freely; never log content.

Call ``configure_logging()`` once at process startup (done in ``main.lifespan``
and reused by workers/tests). It is idempotent.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.types import EventDict, WrappedLogger

# Keys whose *values* must never reach a log sink. Matched case-insensitively.
# Includes both the canonical names from the data-privacy rule and the actual
# Pydantic field names used across the codebase, so redaction fires regardless of
# which layer does the logging.
SENSITIVE_LOG_KEYS: frozenset[str] = frozenset(
    {
        # canonical names from the data-privacy rule
        "email_body",
        "sender_address",
        "thread_context",
        # actual schema / ORM field names carrying raw content or PII
        "body",
        "body_text",
        "body_preview",
        "reply_body",
        "edited_body",
        "approval_note",
        "learning_note",
        "feedback_note",
        "urgency_reason",
        "reason",
        "sender",
        "subject",
        "from_address",
        "to_recipients",
        "cc_recipients",
        "recipients",
        # raw model input/output
        "raw_completion",
        "completion",
        "prompt",
        "messages",
        "content",
    }
)

_REDACTED = "[REDACTED]"

_configured = False


def _redact_value(key: str, value: Any) -> Any:
    """Redact a leaf value if its key is sensitive; recurse into containers."""
    if key.lower() in SENSITIVE_LOG_KEYS:
        return _REDACTED
    if isinstance(value, dict):
        return {k: _redact_value(k, v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        # Preserve container type; redact by *this* key for every element.
        redacted = [_redact_value(key, item) for item in value]
        return type(value)(redacted)
    return value


def redact_sensitive(
    _logger: WrappedLogger,
    _method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """structlog processor: mask any sensitive keys anywhere in the event dict."""
    return {key: _redact_value(key, value) for key, value in event_dict.items()}


def configure_logging(*, environment: str = "local") -> None:
    """Configure structlog process-wide. Idempotent.

    In ``local`` we render human-friendly console output; everywhere else we emit
    JSON so CloudWatch/log aggregators can index the metadata fields.
    """
    global _configured
    if _configured:
        return

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=logging.INFO)

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        # Redaction must run AFTER values are bound and BEFORE rendering.
        redact_sensitive,
        structlog.processors.StackInfoRenderer(),
    ]

    renderer: structlog.types.Processor = (
        structlog.dev.ConsoleRenderer()
        if environment == "local"
        else structlog.processors.JSONRenderer()
    )

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _configured = True
