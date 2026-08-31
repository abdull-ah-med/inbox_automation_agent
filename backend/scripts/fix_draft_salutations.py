"""Rewrite stale draft openings that leak email local-parts (e.g. ``Hi Samplecontact,``).

Deterministic only — uses ``resolve_reply_addressee`` + ``rewrite_opening_salutation``.
Does **not** call the LLM, does **not** send or modify Outlook mail (Mail.Read only).

Targets the latest **pending** draft per open thread (not approved/rejected), and only
rewrites bodies that already start with ``Hi`` / ``Hello`` / ``Hey …,``.

Always applies directory lookup + local-part suppression (even if
``SALUTE_DIRECTORY_ENABLED`` is still false), so you can heal bodies after teaching
contacts without waiting on the flag.

Usage on EC2 (from the backend container):

  docker compose -f docker-compose.yml -f docker-compose.prod.yml exec backend \\
    python -m scripts.fix_draft_salutations

  docker compose -f docker-compose.yml -f docker-compose.prod.yml exec backend \\
    python -m scripts.fix_draft_salutations --apply --mailbox elise@example.com

  # Single draft
  ... python -m scripts.fix_draft_salutations --apply --draft-id <uuid>
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import select

from app.core.config import get_settings
from app.core.draft_salutation import format_opening_salutation, rewrite_opening_salutation
from app.core.reply_addressee import resolve_reply_addressee
from app.db.session import dispose_engine, get_session_factory
from app.models.db.draft import Draft
from app.models.db.thread import Thread
from app.models.schemas.email import (
    EmailDirectionEnum,
    EmailMessageSchema,
    ThreadContextSchema,
    ThreadStateEnum,
)
from app.repositories import draft_repo, message_repo
from app.services import directory_lookup_service

logger = structlog.get_logger(__name__)

_OPENING_GREETING_RE = re.compile(
    r"^(?P<greeting>Hi|Hello|Hey)(?:\s+(?P<name>[^\n,]+))?,[ \t]*",
    re.IGNORECASE,
)

_OPEN_STATES = frozenset(
    {
        ThreadStateEnum.NEW.value,
        ThreadStateEnum.DRAFTED.value,
        ThreadStateEnum.AWAITING_CLIENT.value,
        ThreadStateEnum.AWAITING_VENDOR.value,
        ThreadStateEnum.AWAITING_PARTNER.value,
        ThreadStateEnum.REQUIRES_HUMAN.value,
    }
)


@dataclass(frozen=True)
class _Candidate:
    draft_id: uuid.UUID
    thread_id: uuid.UUID
    mailbox: str
    subject: str
    body: str


def _current_body(edited: str | None, reply: str) -> str:
    return edited if edited is not None and edited.strip() != "" else reply


def _opening_name(body: str) -> str | None:
    match = _OPENING_GREETING_RE.match(body or "")
    if match is None:
        return None
    return (match.group("name") or "").strip()


async def _load_candidates(
    *,
    mailbox: str | None,
    draft_id: uuid.UUID | None,
    limit: int,
    allowed_mailboxes: list[str],
) -> list[_Candidate]:
    factory = get_session_factory()
    async with factory() as session:
        if draft_id is not None:
            draft = await session.get(Draft, draft_id)
            if draft is None:
                return []
            thread = await session.get(Thread, draft.thread_id)
            if thread is None:
                return []
            body = _current_body(draft.edited_body, draft.body)
            return [
                _Candidate(
                    draft_id=draft.id,
                    thread_id=thread.id,
                    mailbox=thread.mailbox,
                    subject=thread.subject,
                    body=body,
                )
            ]

        # Latest pending draft per open thread (PostgreSQL DISTINCT ON).
        filters = [
            Thread.state.in_(list(_OPEN_STATES)),
            Draft.approved_at.is_(None),
            Draft.rejected_at.is_(None),
        ]
        if mailbox:
            filters.append(Thread.mailbox == mailbox.lower())
        elif allowed_mailboxes:
            filters.append(Thread.mailbox.in_([m.lower() for m in allowed_mailboxes]))

        stmt = (
            select(
                Draft.id,
                Draft.thread_id,
                Draft.body,
                Draft.edited_body,
                Thread.mailbox,
                Thread.subject,
            )
            .join(Thread, Thread.id == Draft.thread_id)
            .where(*filters)
            .distinct(Draft.thread_id)
            .order_by(Draft.thread_id, Draft.created_at.desc())
        )

        rows = list((await session.execute(stmt)).all())
        if limit > 0:
            rows = rows[:limit]
        return [
            _Candidate(
                draft_id=row.id,
                thread_id=row.thread_id,
                mailbox=row.mailbox,
                subject=row.subject,
                body=_current_body(row.edited_body, row.body),
            )
            for row in rows
        ]


async def _resolve_salute(
    session,
    *,
    mailbox: str,
    thread_id: uuid.UUID,
    conversation_id: str,
    subject: str,
) -> tuple[str, str, str] | None:
    """Return ``(salute_name, source_kind, email)`` or None when no addressee."""
    messages = await message_repo.list_by_thread(session, thread_id)
    if not messages:
        return None

    schema_messages: list[EmailMessageSchema] = []
    for message in messages:
        direction = (
            EmailDirectionEnum.OUTBOUND
            if str(message.direction).lower() == "outbound"
            else EmailDirectionEnum.INBOUND
        )
        schema_messages.append(
            EmailMessageSchema(
                message_id=message.graph_message_id,
                conversation_id=conversation_id,
                mailbox=mailbox,
                sender=message.sender,
                sender_display_name=getattr(message, "sender_name", None),
                is_automated=bool(getattr(message, "is_automated", False)),
                subject=subject,
                body_text=message.body_text,
                body_preview=message.body_preview,
                received_at=message.received_at,
                direction=direction,
                to_recipients=list(message.to_recipients or []),
                cc_recipients=list(message.cc_recipients or []),
                bcc_recipients=list(message.bcc_recipients or []),
                has_attachments=bool(message.has_attachments),
            )
        )

    settings = get_settings()
    thread_context = ThreadContextSchema(
        conversation_id=conversation_id,
        mailbox=mailbox,
        subject=subject,
        messages=schema_messages,
    )
    directory = await directory_lookup_service.build_directory(
        session,
        mailbox,
        thread_context,
    )
    addressee = resolve_reply_addressee(
        mailbox=mailbox,
        messages=schema_messages,
        mailbox_owner=settings.owner_for_mailbox(mailbox),
        directory=directory,
        suppress_local_part=True,
    )
    if addressee is None:
        return None
    return addressee.salute_name, addressee.source_kind, addressee.email


async def _fix_one(candidate: _Candidate, *, apply: bool) -> str:
    settings = get_settings()
    if not settings.mailbox_allowed(candidate.mailbox):
        return "skipped:mailbox_not_allowed"

    if _OPENING_GREETING_RE.match(candidate.body or "") is None:
        # No Hi/Hello/Hey opening — leave body alone (avoid prepending).
        return "skipped:no_opening_greeting"

    factory = get_session_factory()
    async with factory() as session:
        thread = await session.get(Thread, candidate.thread_id)
        if thread is None:
            return "skipped:thread_missing"

        resolved = await _resolve_salute(
            session,
            mailbox=thread.mailbox,
            thread_id=thread.id,
            conversation_id=thread.conversation_id,
            subject=thread.subject,
        )
        if resolved is None:
            return "skipped:no_addressee"

        salute_name, source_kind, email = resolved
        desired = format_opening_salutation(salute_name)
        new_body = rewrite_opening_salutation(candidate.body, salute_name)
        if new_body == candidate.body:
            return f"ok:unchanged opening={desired!r} source={source_kind} to={email}"

        old_name = _opening_name(candidate.body)
        old_opening = format_opening_salutation(old_name or "")
        summary = (
            f"{old_opening!r} -> {desired!r} source={source_kind} to={email} "
            f"draft={candidate.draft_id}"
        )
        if not apply:
            return f"dry_run:would_rewrite {summary}"

        updated = await draft_repo.set_edited_body(
            session,
            candidate.draft_id,
            edited_body=new_body,
        )
        if updated is None:
            return "failed:draft_missing"
        await session.commit()
        return f"done:rewrote {summary}"


async def _run(
    *,
    apply: bool,
    mailbox: str | None,
    draft_id: uuid.UUID | None,
    limit: int,
) -> int:
    settings = get_settings()
    candidates = await _load_candidates(
        mailbox=mailbox,
        draft_id=draft_id,
        limit=limit,
        allowed_mailboxes=list(settings.mailbox_list),
    )
    scope = f"draft={draft_id}" if draft_id else (mailbox or "all allowed mailboxes")
    print(f"Found {len(candidates)} pending draft(s) on open threads ({scope}).")
    if not candidates:
        await dispose_engine()
        return 0

    results: dict[str, int] = {}
    for candidate in candidates:
        outcome = await _fix_one(candidate, apply=apply)
        bucket = outcome.split(":", 1)[0]
        results[bucket] = results.get(bucket, 0) + 1
        print(
            f"  [{candidate.mailbox}] {candidate.subject!r} "
            f"(thread={candidate.thread_id}) -> {outcome}"
        )

    print("\nSummary:")
    for bucket, count in sorted(results.items()):
        print(f"  {bucket}: {count}")
    if not apply:
        print("\nDry run only — re-run with --apply to persist edited_body.")
        print("No LLM calls; openings only. Mail is never modified.")

    await dispose_engine()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fix pending draft openings that used email local-parts "
            "(Hi Samplecontact,) using directory aliases or bare Hi, — no LLM."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist rewritten openings onto drafts.edited_body. Default is dry run.",
    )
    parser.add_argument(
        "--mailbox",
        default=None,
        help="Only this mailbox (default: all TARGET_MAILBOXES).",
    )
    parser.add_argument(
        "--draft-id",
        default=None,
        help="Fix a single draft UUID (ignores --mailbox / --limit).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Max drafts to process when not using --draft-id (default: 100; 0 = all).",
    )
    args = parser.parse_args(argv)

    draft_id: uuid.UUID | None = None
    if args.draft_id:
        try:
            draft_id = uuid.UUID(args.draft_id)
        except ValueError:
            print(f"Invalid --draft-id: {args.draft_id}", file=sys.stderr)
            return 1

    if args.limit < 0:
        print("--limit must be >= 0", file=sys.stderr)
        return 1

    try:
        return asyncio.run(
            _run(
                apply=args.apply,
                mailbox=args.mailbox,
                draft_id=draft_id,
                limit=args.limit,
            )
        )
    except Exception as exc:
        logger.exception("fix_draft_salutations_failed")
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
