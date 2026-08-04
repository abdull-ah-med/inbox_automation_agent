"""Rebuild email embeddings + search_document from cleaned message bodies.

Selects rows where ``embed_clean_version`` differs from the linked message's
``body_clean_version`` (or where search_document is empty). Dry-run by default.

Usage (from backend/):
  .venv/bin/python -m scripts.reembed_email_embeddings
  .venv/bin/python -m scripts.reembed_email_embeddings --apply
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import structlog
from sqlalchemy import or_, select

from app.core.config import get_settings
from app.core.dependencies import close_openai_client, openai_client_from_settings
from app.db.session import dispose_engine, get_session_factory
from app.llm.email_clean import CLEAN_VERSION, clean_email_body
from app.models.db.email_embedding import EmailEmbedding
from app.models.db.message import Message
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema
from app.repositories import embedding_repo, message_repo, thread_repo
from app.services import embedding_service

logger = structlog.get_logger(__name__)


async def _load_stale_embeddings() -> list[EmailEmbedding]:
    factory = get_session_factory()
    async with factory() as session:
        stmt = (
            select(EmailEmbedding)
            .outerjoin(Message, EmailEmbedding.message_id == Message.id)
            .where(
                or_(
                    EmailEmbedding.search_document == "",
                    EmailEmbedding.embed_clean_version.is_(None),
                    Message.body_clean_version.is_(None),
                    EmailEmbedding.embed_clean_version != Message.body_clean_version,
                    EmailEmbedding.embed_clean_version != CLEAN_VERSION,
                )
            )
        )
        result = await session.execute(stmt)
        rows = list(result.scalars().all())
        for row in rows:
            session.expunge(row)
        return rows


async def _reembed_one(
    row: EmailEmbedding,
    *,
    apply: bool,
) -> str:
    factory = get_session_factory()
    settings = get_settings()
    client = openai_client_from_settings(settings)
    if client is None:
        return "skipped:no_openai"

    email: EmailMessageSchema | None = None
    async with factory() as session:
        if row.message_id is not None:
            msg = await message_repo.get_by_id(session, row.message_id)
            if msg is not None:
                thread = await thread_repo.get_by_id(session, msg.thread_id)
                subject = thread.subject if thread is not None else "(no subject)"
                body_clean = msg.body_clean
                if not body_clean:
                    body_clean = clean_email_body(
                        msg.body_text,
                        content_type=msg.body_content_type or "text",
                    ).body_clean
                email = EmailMessageSchema(
                    message_id=msg.graph_message_id,
                    conversation_id=row.conversation_id,
                    mailbox=row.mailbox,
                    sender=msg.sender,
                    subject=subject,
                    body_text=msg.body_text,
                    body_preview=msg.body_preview,
                    body_content_type=msg.body_content_type or "text",
                    body_clean=body_clean,
                    received_at=msg.received_at,
                    direction=EmailDirectionEnum(msg.direction)
                    if msg.direction in {e.value for e in EmailDirectionEnum}
                    else EmailDirectionEnum.INBOUND,
                    to_recipients=list(msg.to_recipients or []),
                    cc_recipients=list(msg.cc_recipients or []),
                    has_attachments=bool(msg.has_attachments),
                )

    if email is None:
        # Orphan embedding — rebuild search_document from stored preview only.
        email = EmailMessageSchema(
            message_id=f"orphan-{row.id}",
            conversation_id=row.conversation_id,
            mailbox=row.mailbox,
            sender=row.sender_email,
            subject="",
            body_text=row.body_preview or "",
            body_preview=row.body_preview,
            body_content_type="text",
            body_clean=row.body_preview or "",
            received_at=row.sent_at,
            direction=EmailDirectionEnum.INBOUND,
            to_recipients=list(row.recipient_emails or []),
            cc_recipients=list(row.cc_emails or []),
        )

    if not apply:
        return f"dry_run:would_reembed:{row.id}"

    try:
        vector = await embedding_service.embed_email(
            email,
            client=client,
            settings=settings,
        )
        doc = embedding_service.build_search_document(email)
        preview = (email.body_clean or email.body_preview or email.body_text or "")[:500]
        async with factory() as session, session.begin():
            await embedding_repo.update_embedding_document(
                session,
                embedding_id=row.id,
                embedding=vector,
                search_document=doc,
                body_preview=preview or "(empty)",
                embed_clean_version=CLEAN_VERSION,
            )
        return f"done:{row.id}"
    except Exception as exc:  # noqa: BLE001 — CLI surface
        logger.exception("reembed_failed", embedding_id=str(row.id))
        return f"failed:{type(exc).__name__}"


async def _run(apply: bool) -> int:
    get_settings()
    rows = await _load_stale_embeddings()
    print(f"Found {len(rows)} embedding(s) needing reembed.")
    if not rows:
        await dispose_engine()
        return 0

    results: dict[str, int] = {}
    for row in rows:
        outcome = await _reembed_one(row, apply=apply)
        key = outcome.split(":")[0]
        results[key] = results.get(key, 0) + 1
        print(f"  [{row.mailbox}] {row.conversation_id} -> {outcome}")

    print("\nSummary:")
    for outcome, count in sorted(results.items()):
        print(f"  {outcome}: {count}")
    if not apply:
        print("\nDry run only — re-run with --apply to rebuild embeddings.")

    await close_openai_client()
    await dispose_engine()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually re-embed. Without this flag, only prints what would happen.",
    )
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_run(args.apply))
    except Exception as exc:  # noqa: BLE001 — CLI surface
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
