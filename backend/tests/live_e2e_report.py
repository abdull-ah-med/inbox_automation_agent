"""Human-readable report printer for the live E2E pipeline test.

Plain-language stage headers first; technical detail kept underneath.
Does not call Graph, Redis, DB, or Anthropic — print only.
"""

from __future__ import annotations

from app.llm.pii_redact import (
    TOKEN_BANK,
    TOKEN_CARD,
    TOKEN_DL,
    TOKEN_DOB,
    TOKEN_ID,
    TOKEN_SSN,
    scrub_email_for_llm,
    scrub_text,
)
from app.models.schemas.classification import TriageResultSchema
from app.models.schemas.email import EmailMessageSchema, ThreadContextSchema

_WIDTH = 78
_PII_TOKENS = (TOKEN_SSN, TOKEN_DOB, TOKEN_DL, TOKEN_BANK, TOKEN_CARD, TOKEN_ID)

_DRAFT_STATUS_PLAIN = {
    "PENDING": "Continue — this email may need a reply draft next",
    "SKIPPED": "Stop here — no draft needed (spam or no action)",
    "REQUIRES_HUMAN": "Hand off to a person — the AI could not decide",
}

_DEDUP_PLAIN = {
    None: "Not claimed yet",
    "processing": "In progress (claimed so we do not double-work it)",
    "completed": "Finished successfully (will not be re-processed soon)",
}


def truncate(text: str, *, limit: int = 2500) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n… [truncated {len(text) - limit} chars]"


def _rule(char: str = "─") -> None:
    print(char * _WIDTH)


def _blank() -> None:
    print()


def report_banner(title: str) -> None:
    _blank()
    print("#" * _WIDTH)
    print(f"#  {title}")
    print("#" * _WIDTH)


def report_stage(step: int, total: int, title: str, *, meaning: str) -> None:
    _blank()
    _rule("═")
    print(f"  STEP {step} of {total}  ·  {title}")
    print(f"  What this means: {meaning}")
    _rule("═")


def report_section(title: str) -> None:
    _blank()
    print(f"  ┌─ {title}")
    print("  │")


def report_section_end() -> None:
    print("  └──────────────────────────────────────────────────────────────")


def report_kv(label: str, value: object, *, indent: int = 2) -> None:
    pad = " " * indent
    print(f"{pad}{label:<28} {value}")


def report_lines(text: str, *, indent: int = 4, limit: int = 2500) -> None:
    pad = " " * indent
    body = truncate(text, limit=limit)
    for line in body.splitlines() or ["(empty)"]:
        print(f"{pad}{line}")


def report_tech_box(title: str, rows: list[tuple[str, object]]) -> None:
    report_section(f"Technical details — {title}")
    for label, value in rows:
        report_kv(label, value, indent=4)
    report_section_end()


def report_run_header(
    *,
    mailboxes: list[str],
    lookback_days: int,
    max_messages: int | None,
    model: str,
    redis_url: str,
    database_hint: str,
    anthropic_configured: bool,
    slack_configured: bool,
) -> None:
    report_banner("EMAIL PIPELINE TEST REPORT  (ingest → triage → draft → Slack)")
    print()
    print("  This report walks real mailbox email through the system, step by step.")
    print("  Slack review cards post for every action-needed draft")
    print("  (same gate as production — includes an Outlook deep link).")
    _blank()
    report_kv("Mailboxes under test", ", ".join(mailboxes))
    report_kv("How far back we looked", f"{lookback_days} days")
    report_kv(
        "How many emails per mailbox",
        "all (paginated)" if max_messages is None else max_messages,
    )
    report_kv("AI model for sorting", model)
    report_kv("AI key configured", "yes" if anthropic_configured else "NO — Haiku will stop")
    report_kv("Slack configured", "yes" if slack_configured else "NO — cards will be skipped")
    report_kv("Database", database_hint)
    report_kv("Fast memory store (Redis)", redis_url)


def report_email_header(
    *,
    index: int,
    total: int,
    subject: str | None,
    sender: str | None = None,
    received_at: str | None = None,
) -> None:
    report_banner(f"EMAIL {index} of {total}")
    report_kv("Subject", subject or "(no subject)")
    if sender:
        report_kv("From", sender)
    if received_at:
        report_kv("Received", received_at)


def report_fetch_summary(
    *,
    inbox_count: int,
    thread_message_count: int,
    subject: str | None,
) -> None:
    report_stage(
        1,
        7,
        "FETCH FROM MICROSOFT OUTLOOK",
        meaning="We downloaded the email and the rest of its conversation thread.",
    )
    report_kv("Emails found in this batch", inbox_count)
    report_kv("Messages in this conversation", thread_message_count)
    report_kv("Subject", subject or "(no subject)")


def report_conversation_timeline(thread: ThreadContextSchema, *, body_limit: int = 1200) -> None:
    report_section("Conversation timeline (oldest → newest)")
    print(f"  │  {len(thread.messages)} message(s) in this thread")
    print("  │")
    for i, msg in enumerate(thread.messages, start=1):
        direction = "Incoming" if msg.direction.value == "inbound" else "Outgoing (our mailbox)"
        print(f"  │  [{i}/{len(thread.messages)}] {direction}")
        report_kv("From", msg.sender, indent=4)
        report_kv("When", msg.received_at.isoformat(), indent=4)
        report_kv("Subject", msg.subject, indent=4)
        print("    Body:")
        report_lines(msg.body_text, indent=6, limit=body_limit)
        if i < len(thread.messages):
            print("  │")
            print("  │  ·············································")
            print("  │")
    report_section_end()


def report_trigger_tech_ids(email: EmailMessageSchema) -> None:
    report_tech_box(
        "message identifiers",
        [
            ("Mailbox", email.mailbox),
            ("Message ID", email.message_id),
            ("Conversation ID", email.conversation_id),
            ("Direction", email.direction.value),
            ("To", ", ".join(email.to_recipients) or "(none)"),
            ("CC", ", ".join(email.cc_recipients) or "(none)"),
        ],
    )


def _dedup_value_from_snapshot(snapshot: dict[str, str | None]) -> str | None:
    for key, value in snapshot.items():
        if "dedup:" in key:
            return value
    return None


def report_redis_stage(
    *,
    before: dict[str, str | None],
    after_claim: dict[str, str | None],
    after_complete: dict[str, str | None] | None = None,
    ingest_status: str | None = None,
) -> None:
    report_stage(
        3,
        7,
        "REDIS TRACKING (do-not-repeat checklist)",
        meaning=(
            "Redis is a fast checklist so the same email is not processed twice. "
            "We show before → in progress → finished."
        ),
    )
    if ingest_status:
        report_kv("Ingest status code", ingest_status)

    phases: list[tuple[str, dict[str, str | None]]] = [
        ("Before we started", before),
        ("After we claimed it", after_claim),
    ]
    if after_complete is not None:
        phases.append(("After we finished", after_complete))

    for label, snapshot in phases:
        dedup = _dedup_value_from_snapshot(snapshot)
        report_section(label)
        report_kv(
            "Plain status",
            _DEDUP_PLAIN.get(dedup, f"Unknown ({dedup!r})"),
            indent=4,
        )
        print("    Raw keys:")
        if not snapshot:
            print("      (no keys)")
        else:
            for key, value in snapshot.items():
                print(f"      {key} = {value!r}")
        report_section_end()


def report_database_stage(
    *,
    thread_id: str | None,
    conversation_id: str | None,
    mailbox: str,
    subject: str | None,
    state: str | None,
    messages: list[dict[str, object]],
    audit_events: list[dict[str, object]],
    show_message_bodies: bool = True,
    body_limit: int = 800,
) -> None:
    report_stage(
        2,
        7,
        "SAVE TO DATABASE",
        meaning=(
            "We stored the full original emails so people can review them later. "
            "Nothing is hidden in the database."
        ),
    )
    report_kv("Saved?", "Yes" if thread_id and messages else "No / incomplete")
    report_kv("Thread ID", thread_id or "(none)")
    report_kv("Mailbox", mailbox)
    report_kv("Subject", subject or "(none)")
    report_kv("Thread state", state or "(none)")
    report_kv("Messages stored", len(messages))
    report_kv("Audit events so far", len(audit_events))

    if show_message_bodies and messages:
        report_section("What is stored in the database (original text — not privacy-filtered)")
        for i, row in enumerate(messages, start=1):
            print(f"  │  [{i}/{len(messages)}]")
            report_kv("From", row.get("sender"), indent=4)
            report_kv("Direction", row.get("direction"), indent=4)
            report_kv("When", row.get("received_at"), indent=4)
            report_kv("Graph message ID", row.get("graph_message_id"), indent=4)
            print("    Body:")
            report_lines(str(row.get("body_text") or ""), indent=6, limit=body_limit)
            if i < len(messages):
                print("  │")
        report_section_end()

    if audit_events:
        report_section("Audit log entries already present")
        for i, ev in enumerate(audit_events, start=1):
            print(f"  │  [{i}] {ev.get('event_type')}")
            report_kv("When", ev.get("created_at"), indent=4)
            report_kv("Actor", ev.get("actor"), indent=4)
            report_kv("Payload", ev.get("payload"), indent=4)
        report_section_end()
    else:
        print()
        print("  (No audit events yet — those appear after the AI decision.)")

    report_tech_box(
        "database identifiers",
        [
            ("thread_id", thread_id),
            ("conversation_id", conversation_id),
            ("mailbox", mailbox),
        ],
    )


def _pii_tokens_found(text: str) -> list[str]:
    return [token for token in _PII_TOKENS if token in text]


def report_pii_stage(
    *,
    trigger: EmailMessageSchema,
    thread: ThreadContextSchema,
    user_content: str,
) -> None:
    report_stage(
        4,
        7,
        "PRIVACY FILTER (before AI)",
        meaning=(
            "We hide sensitive numbers (SSN, bank account, card, etc.) before sending "
            "text to the AI. The database still keeps the original for human review."
        ),
    )

    original_blob = "\n".join([trigger.body_text, *(m.body_text for m in thread.messages)])
    scrubbed_trigger = scrub_email_for_llm(trigger)
    scrubbed_blob = "\n".join(
        [
            scrubbed_trigger.body_text,
            *(scrub_email_for_llm(m).body_text for m in thread.messages),
        ]
    )
    tokens = _pii_tokens_found(scrubbed_blob)
    changed = scrubbed_blob != original_blob

    # Also show if scrub_text alone would change a sample (for labeled PII).
    sample_scrub = scrub_text(trigger.body_text)
    sample_tokens = _pii_tokens_found(sample_scrub)

    report_kv(
        "Did anything get hidden?",
        "Yes" if changed or sample_tokens else "No — nothing matched",
    )
    report_kv(
        "Privacy markers applied",
        ", ".join(dict.fromkeys(tokens + sample_tokens)) or "(none)",
    )
    report_kv("Database still has originals?", "Yes (verified separately by the test)")

    report_section("Exact text the AI will see (privacy-filtered)")
    report_lines(user_content, indent=4, limit=6000)
    report_section_end()

    if changed or sample_tokens:
        report_section("Trigger email body AFTER privacy filter")
        report_lines(scrubbed_trigger.body_text, indent=4, limit=2000)
        report_section_end()
    else:
        print()
        print("  Note: No SSN/bank/card-style patterns were found in this email.")
        print("  The AI still receives a cleaned copy of the same content.")


def report_haiku_stage(
    *,
    model: str | None,
    prompt_version: str | None,
    draft_status: str | None,
    triage: TriageResultSchema | None,
    latency_ms: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> None:
    report_stage(
        5,
        7,
        "AI TRIAGE (Claude Haiku)",
        meaning=(
            "A small AI reads the privacy-filtered email and decides: spam or not, "
            "needs a reply or not, and whether earlier context is required."
        ),
    )

    if triage is None:
        report_kv("Result", "FAILED — AI did not return a usable decision")
        report_kv("Next step", _DRAFT_STATUS_PLAIN.get(draft_status or "", draft_status))
        report_tech_box(
            "triage call",
            [
                ("Model", model),
                ("Prompt version", prompt_version),
                ("Draft status", draft_status),
            ],
        )
        return

    report_kv("Is this spam / junk?", "Yes" if triage.is_spam else "No")
    if triage.spam_reason:
        report_kv("Spam reason", triage.spam_reason)
    report_kv("Does someone need to act?", "Yes" if triage.has_action_items else "No")
    if triage.action_items_summary:
        report_kv("What action?", triage.action_items_summary)
    report_kv(
        "Needs more conversation context?",
        "Yes" if triage.needs_context else "No",
    )
    if triage.context_reason:
        report_kv("Context reason", triage.context_reason)
    report_kv(
        "What happens next?",
        _DRAFT_STATUS_PLAIN.get(draft_status or "", draft_status or "(unknown)"),
    )

    report_tech_box(
        "Haiku call",
        [
            ("Model", model),
            ("Prompt version", prompt_version),
            ("Draft status code", draft_status),
            ("Latency (ms)", latency_ms if latency_ms is not None else "(n/a)"),
            ("Input tokens", input_tokens if input_tokens is not None else "(n/a)"),
            ("Output tokens", output_tokens if output_tokens is not None else "(n/a)"),
            ("is_spam", triage.is_spam),
            ("has_action_items", triage.has_action_items),
            ("needs_context", triage.needs_context),
        ],
    )


def report_audit_stage(*, audit_events: list[dict[str, object]]) -> None:
    report_stage(
        6,
        7,
        "AUDIT TRAIL",
        meaning="We write a short decision record so later we can see why the system acted.",
    )
    if not audit_events:
        print("  No audit events found yet.")
        return
    for i, ev in enumerate(audit_events, start=1):
        report_section(f"Event {i}")
        report_kv("Type", ev.get("event_type"), indent=4)
        report_kv("When", ev.get("created_at"), indent=4)
        report_kv("Actor", ev.get("actor"), indent=4)
        report_kv("Details", ev.get("payload"), indent=4)
        report_section_end()


def report_redis_final(snapshot: dict[str, str | None]) -> None:
    """Short follow-up after Haiku marks the checklist complete."""
    _blank()
    _rule("─")
    print("  REDIS UPDATE — after AI finished")
    dedup = _dedup_value_from_snapshot(snapshot)
    report_kv("Plain status", _DEDUP_PLAIN.get(dedup, f"Unknown ({dedup!r})"))
    for key, value in snapshot.items():
        if "dedup:" in key:
            print(f"    {key} = {value!r}")
    _rule("─")


def report_haiku_blocked() -> None:
    report_stage(
        5,
        7,
        "AI TRIAGE BLOCKED",
        meaning="Everything above worked. The AI step cannot run without an API key.",
    )
    print("  Fix: put a real ANTHROPIC_API_KEY in backend/.env")
    print("  Then: unset ANTHROPIC_API_KEY && set -a && source .env && set +a")
    print("  Re-run: RUN_LIVE_E2E=1 .venv/bin/pytest tests/test_live_e2e_pipeline.py -vv -s")


def report_skip_triage(status: str) -> None:
    _blank()
    _rule("═")
    print(f"  SKIPPED AI TRIAGE — ingest status was {status!r}")
    print("  What this means: This email was already finished or is still locked.")
    print("  Tip: leave LIVE_E2E_KEEP_DEDUP unset so the test clears Redis and re-runs.")
    _rule("═")


def report_slack_stage(
    *,
    posted: bool,
    skipped_reason: str | None,
    message_ts: str | None,
) -> None:
    report_stage(
        7,
        7,
        "SLACK REVIEW CARD",
        meaning=(
            "When the draft is ready (DRAFTED), we post a human review card "
            "with an Outlook link (same as production)."
        ),
    )
    if posted:
        report_kv("Posted to Slack", "yes")
        report_kv("Slack message_ts", message_ts)
    else:
        report_kv("Posted to Slack", "no")
        report_kv("Why skipped", skipped_reason or "(unknown)")


def report_finale(
    *,
    mailboxes: list[str],
    slack_posted_count: int,
    json_path: str | None = None,
) -> None:
    report_banner("TEST COMPLETE")
    print()
    print("  Plain summary: Microsoft fetch → save → Redis → privacy filter →")
    print("  Haiku triage → Sonnet draft (when needed) → Slack card when")
    print("  action-needed and draft_status=DRAFTED.")
    report_kv("Mailboxes processed", ", ".join(mailboxes))
    report_kv("Slack cards posted", slack_posted_count)
    if json_path:
        _blank()
        print(f"  Full structured output (JSON): {json_path}")
    _blank()
    print("  How to inspect yourself:")
    print("    Database:")
    print("      SELECT subject, state FROM threads;")
    print("      SELECT sender, direction, left(body_text,80) FROM messages;")
    print("      SELECT event_type, payload FROM audit_events ORDER BY created_at DESC LIMIT 20;")
    print("    Redis:")
    for mailbox in mailboxes:
        print(f"      redis-cli -n 0 KEYS 'dedup:{mailbox}:*'")
        print(f"      redis-cli -n 0 KEYS 'slack:posted:{mailbox}:*'")
    _blank()
