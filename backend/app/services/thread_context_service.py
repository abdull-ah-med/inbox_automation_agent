"""Orchestrate ADD-only thread fact extract off the draft hot path."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

import structlog
from anthropic import AsyncAnthropic
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.exceptions import ThreadNotFoundError
from app.core.internal_mail import extract_email_address
from app.llm import thread_facts as thread_facts_llm
from app.llm.thread_fact_quality import (
    apply_person_aliases,
    is_useful_thread_fact,
    person_aliases_from_fact_texts,
    person_aliases_from_messages,
)
from app.models.schemas.thread_context import ThreadContextFactView, ThreadContextView
from app.repositories import message_repo, thread_context_repo, thread_repo
from app.repositories.message_repo import MessageSchema
from app.repositories.thread_context_repo import FactRow

logger = structlog.get_logger(__name__)

COVERAGE_EXTRA_CALL_CAP = 2
EXTRACT_STALE_AFTER = timedelta(minutes=5)
ExtractOutcome = Literal["ran", "skipped", "locked", "disabled", "failed"]


def _extract_hash(*, last_message_id: uuid.UUID | None, user_notes: str, message_count: int) -> str:
    payload = f"{last_message_id or ''}|{message_count}|{user_notes}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _try_extract_lock(session: AsyncSession, thread_id: uuid.UUID) -> bool:
    result = await session.execute(
        text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"),
        {"key": f"thread_context:{thread_id}"},
    )
    locked = result.scalar()
    return bool(locked)


def _uncovered_priority_messages(
    messages: list[MessageSchema],
    cited: set[uuid.UUID],
) -> list[MessageSchema]:
    inbound = [m for m in messages if m.direction == "inbound"]
    outbound = [m for m in messages if m.direction == "outbound"]
    needed: list[MessageSchema] = []
    if inbound and inbound[0].id not in cited:
        needed.append(inbound[0])
    for message in outbound:
        if message.id not in cited:
            needed.append(message)
    return needed


def _aliases_for_thread(
    messages: list[MessageSchema],
    fact_bodies: list[str],
    *,
    mailbox: str | None = None,
    mailbox_owner: str | None = None,
    directory: dict[str, str] | None = None,
) -> dict[str, str]:
    aliases = person_aliases_from_messages(
        messages,
        mailbox=mailbox,
        mailbox_owner=mailbox_owner,
        directory=directory,
    )
    aliases.update(person_aliases_from_fact_texts(fact_bodies))
    if mailbox and mailbox_owner:
        owner_key = (extract_email_address(mailbox) or mailbox).strip().lower()
        owner = mailbox_owner.strip()
        if owner_key and owner:
            aliases[owner_key] = owner
    return aliases


def _present_facts(
    rows: list[FactRow],
    aliases: dict[str, str],
    messages: list[MessageSchema],
) -> list[ThreadContextFactView]:
    by_id = {message.id: message for message in messages}
    visible: list[ThreadContextFactView] = []
    for row in rows:
        body = apply_person_aliases(row.body, aliases)
        if not is_useful_thread_fact(body):
            continue
        source = by_id.get(row.source_message_id) if row.source_message_id else None
        visible.append(
            ThreadContextFactView(
                id=row.id,
                body=body,
                source_message_id=row.source_message_id,
                created_at=row.created_at,
                source_received_at=source.received_at if source is not None else None,
            )
        )
    visible.sort(
        key=lambda fact: (
            fact.source_received_at or fact.created_at or datetime.min.replace(tzinfo=UTC)
        ),
        reverse=True,
    )
    return visible


def _extract_progress(
    pointer: thread_context_repo.ThreadContextRow, *, now: datetime
) -> tuple[bool, str | None]:
    status = (pointer.extract_status or "idle").strip().lower()
    error = pointer.last_extract_error
    started = pointer.extract_started_at
    if status == "running":
        if started is None:
            return True, None
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        if now - started > EXTRACT_STALE_AFTER:
            return False, "Rebuild timed out. Click Rebuild facts to try again."
        return True, None
    if status == "failed":
        return False, error or "Rebuild failed. Try again."
    return False, None


def _needs_initial_extract(
    pointer: thread_context_repo.ThreadContextRow | None,
    *,
    in_progress: bool,
    rebuild_error: str | None,
) -> bool:
    if in_progress or rebuild_error:
        return False
    if pointer is None:
        return True
    return not (pointer.extract_input_hash or "").strip()


async def _salute_directory(
    session: AsyncSession,
    settings: Settings,
    mailbox: str | None,
    messages: list[MessageSchema],
) -> dict[str, str] | None:
    if not settings.salute_directory_enabled or not mailbox:
        return None
    from app.repositories import mailbox_contact_repo

    emails: list[str] = []
    seen: set[str] = set()
    for message in messages:
        for raw in [message.sender, *(message.to_recipients or []), *(message.cc_recipients or [])]:
            extracted = extract_email_address(raw)
            if extracted is None or extracted in seen:
                continue
            seen.add(extracted)
            emails.append(extracted)
    mailbox_email = extract_email_address(mailbox)
    if mailbox_email and mailbox_email not in seen:
        emails.append(mailbox_email)
    if not emails:
        return {}
    rows = await mailbox_contact_repo.get_many(session, emails)
    return {email: row.first_name for email, row in rows.items()}


async def present_context(
    session: AsyncSession,
    thread_id: uuid.UUID,
    settings: Settings | None = None,
) -> ThreadContextView:
    settings = settings or get_settings()
    pointer = await thread_context_repo.get(session, thread_id)
    facts = await thread_context_repo.list_active_facts(session, thread_id)
    messages = await message_repo.list_by_thread(session, thread_id)
    thread = await thread_repo.get_by_id_trusted(session, thread_id)
    mailbox = thread.mailbox if thread is not None else None
    owner = settings.owner_for_mailbox(mailbox) if mailbox else None
    directory = await _salute_directory(session, settings, mailbox, messages)
    aliases = _aliases_for_thread(
        messages,
        [row.body for row in facts],
        mailbox=mailbox,
        mailbox_owner=owner,
        directory=directory,
    )
    if pointer is None:
        return ThreadContextView(
            version=0,
            user_notes="",
            facts=_present_facts(facts, aliases, messages),
            needs_initial_extract=True,
        )
    in_progress, rebuild_error = _extract_progress(pointer, now=datetime.now(UTC))
    return ThreadContextView(
        version=pointer.version,
        user_notes=pointer.user_notes,
        updated_at=pointer.updated_at,
        facts=_present_facts(facts, aliases, messages),
        rebuild_in_progress=in_progress,
        rebuild_error=rebuild_error,
        needs_initial_extract=_needs_initial_extract(
            pointer, in_progress=in_progress, rebuild_error=rebuild_error
        ),
    )


async def begin_rebuild(session: AsyncSession, thread_id: uuid.UUID) -> bool:
    """Mark extract running. False if a non-stale rebuild is already in flight."""
    await thread_context_repo.get_or_create(session, thread_id)
    return await thread_context_repo.cas_begin_rebuild(
        session,
        thread_id,
        now=datetime.now(UTC),
        stale_after=EXTRACT_STALE_AFTER,
    )


async def finish_rebuild(session: AsyncSession, thread_id: uuid.UUID, *, error: str | None) -> None:
    await thread_context_repo.set_extract_status(
        session,
        thread_id,
        status="failed" if error else "idle",
        started_at=None,
        error=error,
    )


async def complete_rebuild_after_extract(
    session: AsyncSession,
    thread_id: uuid.UUID,
    outcome: ExtractOutcome,
) -> None:
    """Idle/fail the pointer after extract. A lock miss leaves the winner's running row."""
    if outcome == "locked":
        return
    error = "Rebuild failed. Try again." if outcome == "failed" else None
    await finish_rebuild(session, thread_id, error=error)


async def extract_if_needed(
    session: AsyncSession,
    thread_id: uuid.UUID,
    *,
    client: AsyncAnthropic | None,
    settings: Settings,
    source: str,
    force: bool = False,
) -> ExtractOutcome:
    """Hash-short-circuit Haiku extract. Never raises into HTTP."""
    if settings.thread_context_enabled is not True or client is None:
        return "disabled"
    try:
        return await _extract_if_needed(
            session,
            thread_id,
            client=client,
            settings=settings,
            source=source,
            force=force,
        )
    except Exception:
        logger.exception("thread_context.extract_failed", thread_id=str(thread_id), source=source)
        return "failed"


async def _extract_if_needed(
    session: AsyncSession,
    thread_id: uuid.UUID,
    *,
    client: AsyncAnthropic,
    settings: Settings,
    source: str,
    force: bool,
) -> ExtractOutcome:
    thread = await thread_repo.get_by_id_trusted(session, thread_id)
    if thread is None:
        return "skipped"
    messages = await message_repo.list_by_thread(session, thread_id)
    if not messages:
        return "skipped"

    pointer = await thread_context_repo.get_or_create(session, thread_id)
    digest = _extract_hash(
        last_message_id=messages[-1].id,
        user_notes=pointer.user_notes,
        message_count=len(messages),
    )
    if not force and pointer.extract_input_hash == digest:
        logger.info(
            "thread_context.extract",
            thread_id=str(thread_id),
            source=source,
            hash_skipped=True,
            fact_count=len(await thread_context_repo.list_active_facts(session, thread_id)),
            coverage_extra_calls=0,
        )
        return "skipped"

    if not await _try_extract_lock(session, thread_id):
        return "locked"

    allowed = {m.id for m in messages}
    owner = settings.owner_for_mailbox(thread.mailbox)
    directory = await _salute_directory(session, settings, thread.mailbox, messages)
    new_facts = await thread_facts_llm.extract_facts(
        client,
        settings,
        messages,
        allowed_message_ids=allowed,
        mailbox=thread.mailbox,
        mailbox_owner=owner,
        directory=directory,
    )
    await _persist_new_facts(
        session,
        thread_id,
        new_facts,
        messages=messages,
        mailbox=thread.mailbox,
        mailbox_owner=owner,
        directory=directory,
    )

    extra_calls = 0
    active = await thread_context_repo.list_active_facts(session, thread_id)
    cited = {row.source_message_id for row in active if row.source_message_id is not None}
    for message in _uncovered_priority_messages(messages, cited):
        if extra_calls >= COVERAGE_EXTRA_CALL_CAP:
            break
        focused = await thread_facts_llm.extract_facts(
            client,
            settings,
            [message],
            allowed_message_ids={message.id},
            mailbox=thread.mailbox,
            mailbox_owner=owner,
            directory=directory,
        )
        extra_calls += 1
        await _persist_new_facts(
            session,
            thread_id,
            focused,
            messages=messages,
            mailbox=thread.mailbox,
            mailbox_owner=owner,
            directory=directory,
        )
        if any(f.get("source_message_id") == message.id for f in focused):
            cited.add(message.id)

    await thread_context_repo.set_extract_hash(
        session,
        thread_id,
        extract_hash=digest,
        last_message_id=messages[-1].id,
    )
    active = await thread_context_repo.list_active_facts(session, thread_id)
    logger.info(
        "thread_context.extract",
        thread_id=str(thread_id),
        source=source,
        hash_skipped=False,
        fact_count=len(active),
        coverage_extra_calls=extra_calls,
    )
    return "ran"


async def _persist_new_facts(
    session: AsyncSession,
    thread_id: uuid.UUID,
    facts: list[dict],
    *,
    messages: list[MessageSchema],
    mailbox: str | None = None,
    mailbox_owner: str | None = None,
    directory: dict[str, str] | None = None,
) -> None:
    if not facts:
        return
    active = await thread_context_repo.list_active_facts(session, thread_id)
    aliases = _aliases_for_thread(
        messages,
        [row.body for row in active] + [str(f.get("text") or "") for f in facts],
        mailbox=mailbox,
        mailbox_owner=mailbox_owner,
        directory=directory,
    )
    seen = {(row.body, row.source_message_id) for row in active}
    to_add: list[dict] = []
    for fact in facts:
        body = apply_person_aliases(str(fact.get("text") or "").strip(), aliases)
        source_id = fact.get("source_message_id")
        if not body or not is_useful_thread_fact(body) or (body, source_id) in seen:
            continue
        seen.add((body, source_id))
        to_add.append(
            {
                "body": body,
                "source_message_id": source_id,
                "actor_kind": "llm",
            }
        )
    if to_add:
        await thread_context_repo.add_facts(session, thread_id, to_add)


async def load_draft_working_memory(
    session: AsyncSession,
    thread_id: uuid.UUID,
    *,
    settings: Settings,
    mailbox: str,
    recipient: str | None,
) -> dict:
    """Pins, facts, prior sends, and org identity for the draft pack."""
    empty = {
        "user_notes": "",
        "facts": [],
        "prior_sends": [],
        "org_identity": (settings.org_identity or "").strip(),
    }
    if settings.thread_context_enabled is not True:
        empty["org_identity"] = ""
        return empty
    pointer = await thread_context_repo.get(session, thread_id)
    facts = await thread_context_repo.list_active_facts(session, thread_id)
    messages = await message_repo.list_by_thread(session, thread_id)
    directory = await _salute_directory(session, settings, mailbox, messages)
    aliases = _aliases_for_thread(
        messages,
        [row.body for row in facts],
        mailbox=mailbox,
        mailbox_owner=settings.owner_for_mailbox(mailbox),
        directory=directory,
    )
    notes = pointer.user_notes if pointer is not None else ""
    prior: list[dict] = []
    if recipient and recipient.strip():
        rows = await message_repo.list_recent_outbound_to_recipient(
            session,
            mailbox=mailbox,
            recipient=recipient.strip(),
            exclude_thread_id=thread_id,
            limit=3,
        )
        prior = [
            {"body": (row.body_clean or row.body_text or "").strip(), "to": recipient}
            for row in rows
            if (row.body_clean or row.body_text or "").strip()
        ]
    presented = _present_facts(facts, aliases, messages)
    return {
        "user_notes": notes,
        "facts": [
            {"text": row.body, "source_message_id": str(row.source_message_id or "")}
            for row in presented
        ],
        "prior_sends": prior,
        "org_identity": (settings.org_identity or "").strip(),
    }


async def save_user_notes(
    session: AsyncSession,
    thread_id: uuid.UUID,
    *,
    notes: str,
    expected_version: int,
):
    await thread_context_repo.get_or_create(session, thread_id)
    return await thread_context_repo.save_user_notes(
        session,
        thread_id,
        notes=notes,
        expected_version=expected_version,
    )


async def discard_fact(session: AsyncSession, thread_id: uuid.UUID, fact_id: uuid.UUID) -> None:
    active = await thread_context_repo.list_active_facts(session, thread_id)
    if fact_id not in {row.id for row in active}:
        raise ThreadNotFoundError(f"Fact not found: {fact_id}")
    await thread_context_repo.discard_facts(session, [fact_id])
