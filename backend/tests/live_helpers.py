"""Shared helpers for opt-in live Graph / Claude tests (print-friendly)."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from app.core.config import Settings
from app.llm.pii_redact import scrub_email_for_llm, scrub_thread_for_llm
from app.models.schemas.email import EmailMessageSchema, ThreadContextSchema
from app.models.schemas.graph import GraphMessageSchema
from app.services.ingestion_service import _to_email_message_schema


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def max_messages(default: int | None = 3) -> int | None:
    """Max messages to process. ``None`` / env ``all`` means no cap (paginate Graph)."""
    raw = os.environ.get("LIVE_MAX_MESSAGES", "").strip().lower()
    if not raw:
        return default
    if raw in {"0", "all", "*"}:
        return None
    try:
        return max(1, min(int(raw), 200))
    except ValueError:
        return default


def resolve_e2e_mailboxes(settings: Settings) -> list[str]:
    """Mailboxes for live E2E: ``TEST_MAILBOXES`` if set, else ``TARGET_MAILBOXES``."""
    raw = os.environ.get("TEST_MAILBOXES", "").strip()
    if raw:
        mailboxes = [m.strip() for m in raw.split(",") if m.strip()]
        if mailboxes:
            return mailboxes
    if not settings.mailbox_list:
        raise RuntimeError(
            "TEST_MAILBOXES or TARGET_MAILBOXES required in .env for live mailbox tests"
        )
    return list(settings.mailbox_list)


def require_graph_settings() -> Settings:
    settings = Settings()
    if not (settings.graph_client_id and settings.graph_client_secret and settings.graph_tenant_id):
        raise RuntimeError(
            "GRAPH_CLIENT_ID / GRAPH_CLIENT_SECRET / GRAPH_TENANT_ID required in .env"
        )
    if not settings.mailbox_list and not os.environ.get("TEST_MAILBOXES", "").strip():
        raise RuntimeError("TARGET_MAILBOXES or TEST_MAILBOXES required in .env")
    return settings


def require_anthropic_settings() -> Settings:
    settings = require_graph_settings()
    if not settings.anthropic_api_key.strip():
        raise RuntimeError("ANTHROPIC_API_KEY required in .env for live Claude tests")
    return settings


def require_openai_settings() -> Settings:
    """Settings for live embedding / tone-memory tests (OpenAI + Anthropic + DB).

    Graph credentials are optional here — cross-thread Graph fetch can use a
    stub client. Mailbox list is still required so simulate ingest passes the
    allowlist.
    """
    settings = Settings()
    if not settings.openai_api_key.strip():
        raise RuntimeError("OPENAI_API_KEY required in .env for live embedding tests")
    if not settings.anthropic_api_key.strip():
        raise RuntimeError("ANTHROPIC_API_KEY required in .env for live embedding E2E")
    if not settings.mailbox_list:
        raise RuntimeError("TARGET_MAILBOXES required in .env for live embedding E2E")
    return settings


def lookback_days(default: int = 30) -> int:
    """How far back to list Graph messages (default 30 days for E2E)."""
    raw = os.environ.get("LIVE_LOOKBACK_DAYS", "").strip()
    if not raw:
        return default
    try:
        return max(1, min(int(raw), 365))
    except ValueError:
        return default


def lookback_filter(*, days: int | None = None) -> str:
    window = lookback_days(30) if days is None else days
    since = datetime.now(UTC) - timedelta(days=window)
    return f"receivedDateTime ge {since.strftime('%Y-%m-%dT%H:%M:%SZ')}"


def truncate(text: str, *, limit: int = 2500) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n… [truncated {len(text) - limit} chars]"


def graph_to_email(
    mailbox: str,
    conversation_id: str,
    message: GraphMessageSchema,
) -> EmailMessageSchema:
    """Same conversion the ingest pipeline uses."""
    return _to_email_message_schema(
        mailbox=mailbox,
        conversation_id=conversation_id,
        message=message,
    )


def build_thread_context(
    *,
    mailbox: str,
    conversation_id: str,
    subject: str,
    thread_messages: list[GraphMessageSchema],
) -> ThreadContextSchema:
    emails = [graph_to_email(mailbox, conversation_id, msg) for msg in thread_messages]
    return ThreadContextSchema(
        conversation_id=conversation_id,
        mailbox=mailbox,
        subject=subject or "(no subject)",
        messages=emails,
    )


def print_divider(title: str) -> None:
    print(f"\n{'=' * 72}")
    print(title)
    print("=" * 72)


def print_email(email: EmailMessageSchema, *, label: str = "EMAIL") -> None:
    print(f"\n--- {label} ---")
    print(f"  message_id:      {email.message_id}")
    print(f"  conversation_id: {email.conversation_id}")
    print(f"  mailbox:         {email.mailbox}")
    print(f"  direction:       {email.direction.value}")
    print(f"  sender:          {email.sender}")
    print(f"  to:              {', '.join(email.to_recipients) or '(none)'}")
    print(f"  cc:              {', '.join(email.cc_recipients) or '(none)'}")
    print(f"  subject:         {email.subject}")
    print(f"  received_at:     {email.received_at.isoformat()}")
    preview = email.body_preview or ""
    print(f"  body_preview:    {truncate(preview, limit=400)!r}")
    print("  body_text:")
    for line in truncate(email.body_text).splitlines() or ["(empty)"]:
        print(f"    {line}")


def print_thread(thread: ThreadContextSchema, *, redacted: bool) -> None:
    shown = scrub_thread_for_llm(thread) if redacted else thread
    tag = "REDACTED" if redacted else "RAW"
    print(f"\n--- THREAD ({tag}) conversation_id={shown.conversation_id} ---")
    print(f"  mailbox: {shown.mailbox}")
    print(f"  subject: {shown.subject}")
    print(f"  message_count: {len(shown.messages)}")
    for i, msg in enumerate(shown.messages, start=1):
        print_email(msg, label=f"THREAD MSG {i}/{len(shown.messages)}")


def print_scrubbed_pair(email: EmailMessageSchema, thread: ThreadContextSchema) -> None:
    """Show before/after scrubbing so PII redaction is tangible."""
    scrubbed_email = scrub_email_for_llm(email)
    scrubbed_thread = scrub_thread_for_llm(thread)
    print_divider("ORIGINAL (as ingested from Graph — truncated)")
    print_email(email, label="ORIGINAL TRIGGER EMAIL")
    print_thread(thread, redacted=False)
    print_divider("REDACTED (what we send toward Claude)")
    print_email(scrubbed_email, label="SCRUBBED TRIGGER EMAIL")
    print_thread(scrubbed_thread, redacted=False)


def print_redis_keys(redis_snapshot: dict[str, str | None], *, title: str) -> None:
    print_divider(title)
    if not redis_snapshot:
        print("  (no keys)")
        return
    for key, value in redis_snapshot.items():
        print(f"  {key} = {value!r}")


def print_db_thread_rows(
    *,
    thread_id: str | None,
    conversation_id: str | None,
    mailbox: str,
    subject: str | None,
    state: str | None,
    messages: list[dict[str, object]],
    audit_events: list[dict[str, object]],
) -> None:
    print_divider("POSTGRES — thread / messages / audit")
    print(f"  thread.id:            {thread_id}")
    print(f"  thread.mailbox:       {mailbox}")
    print(f"  thread.conversation:  {conversation_id}")
    print(f"  thread.subject:       {subject}")
    print(f"  thread.state:         {state}")
    print(f"  messages in thread:   {len(messages)}")
    for i, row in enumerate(messages, start=1):
        preview = str(row.get("body_preview") or "")[:120]
        print(
            f"    [{i}] graph_id={row.get('graph_message_id')} "
            f"dir={row.get('direction')} sender={row.get('sender')} "
            f"received={row.get('received_at')} preview={preview!r}"
        )
        body = str(row.get("body_text") or "")
        for line in truncate(body, limit=800).splitlines() or ["(empty)"]:
            print(f"         {line}")
    print(f"  audit_events:         {len(audit_events)}")
    for i, ev in enumerate(audit_events, start=1):
        print(
            f"    [{i}] type={ev.get('event_type')} "
            f"actor={ev.get('actor')} created={ev.get('created_at')} "
            f"payload={ev.get('payload')}"
        )


def print_haiku_result(
    *,
    model: str | None,
    prompt_version: str | None,
    draft_status: str | None,
    triage: object | None,
    latency_ms: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> None:
    print_divider("HAIKU TRIAGE RESULT")
    print(f"  model:                {model}")
    print(f"  prompt_version:       {prompt_version}")
    print(f"  draft_status:         {draft_status}")
    if latency_ms is not None:
        print(f"  latency_ms:           {latency_ms}")
    if input_tokens is not None:
        print(f"  input_tokens:         {input_tokens}")
    if output_tokens is not None:
        print(f"  output_tokens:        {output_tokens}")
    if triage is None:
        print("  triage:               None (failed / requires human)")
        return
    print(f"  is_spam:              {getattr(triage, 'is_spam', None)}")
    print(f"  spam_reason:          {getattr(triage, 'spam_reason', None)!r}")
    print(f"  has_action_items:     {getattr(triage, 'has_action_items', None)}")
    print(f"  action_items_summary: {getattr(triage, 'action_items_summary', None)!r}")
    print(f"  needs_context:        {getattr(triage, 'needs_context', None)}")
    print(f"  context_reason:       {getattr(triage, 'context_reason', None)!r}")
