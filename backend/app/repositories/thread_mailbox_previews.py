"""Mailbox card preview queries (keeps thread_repo under LOC budget)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.mailbox_keys import infer_mailbox_key
from app.core.outlook_links import outlook_web_link
from app.models.db.message import Message
from app.models.db.thread import Thread
from app.models.schemas.dashboard import ThreadSummary
from app.repositories import audit_repo, draft_repo, message_repo
from app.repositories.thread_disposition_sql import latest_draft_ranked, needs_elise_action
from app.repositories.thread_list_helpers import (
    card_preview,
    display_state,
    enriched_triage,
    party_sender,
)


async def _enrich_preview_buckets(
    session: AsyncSession,
    *,
    buckets: dict[str, list[ThreadSummary]],
    pending: list[tuple[str, uuid.UUID, str]],
    pairs: list[tuple[str, str]],
    thread_ids: list[uuid.UUID],
) -> None:
    from app.repositories import thread_repo

    triage_map = await audit_repo.triage_flags_by_conversations(session, pairs)
    automated_map = await message_repo.inbound_automated_by_threads(session, thread_ids)
    draft_ids = await thread_repo._thread_ids_with_drafts(session, thread_ids)
    letter_ids = await thread_repo._thread_ids_with_letters(session, thread_ids)
    teaching_notes = await draft_repo.latest_teaching_notes_by_threads(session, thread_ids)
    finished = await draft_repo.review_finished_by_threads(session, thread_ids)
    provenance = await audit_repo.resolution_provenance_by_conversations(session, pairs)
    for mailbox, thread_id, conversation_id in pending:
        key = mailbox.lower()
        for i, item in enumerate(buckets[key]):
            if item.id != thread_id:
                continue
            prov = provenance.get((mailbox, conversation_id), {})
            buckets[key][i] = thread_repo.with_presentation(
                item.model_copy(
                    update={
                        "triage": enriched_triage(
                            triage_map.get((mailbox, conversation_id)),
                            mailbox=mailbox,
                            last_sender=item.last_sender,
                            subject=item.subject,
                            is_automated=automated_map.get(thread_id),
                        ),
                        "has_draft": thread_id in draft_ids,
                        "has_letter": thread_id in letter_ids,
                        "teaching_note": teaching_notes.get(thread_id),
                    }
                ),
                draft_review_finished=thread_id in finished,
                resolved_by=prov.get("resolved_by"),
                resolution_reason=prov.get("resolution_reason"),
                resolution_summary=prov.get("resolution_summary"),
                forced_disposition=prov.get("forced_disposition"),
            )
            break


async def list_recent_for_mailboxes(
    session: AsyncSession,
    mailbox_emails: list[str],
    *,
    per_mailbox: int = 3,
    stale_after_hours: int = 24,
) -> dict[str, list[ThreadSummary]]:
    """Top ``per_mailbox`` awaiting-action threads per mailbox.

    Ranked independently per mailbox so a busy inbox cannot starve the
    others on the home-page cards.
    """
    from app.repositories import thread_repo

    _ = stale_after_hours  # reserved for future stale filtering parity
    if not mailbox_emails or per_mailbox < 1:
        return {email: [] for email in mailbox_emails}

    now = datetime.now(UTC)
    msg_count = func.count(Message.id).label("message_count")
    latest_msg = thread_repo._latest_message_ranked()
    latest_draft = latest_draft_ranked()
    ranked = (
        select(
            Thread.id.label("tid"),
            func.row_number()
            .over(
                partition_by=Thread.mailbox,
                order_by=(Thread.last_message_at.desc().nullslast(), Thread.id.desc()),
            )
            .label("rn"),
        )
        .outerjoin(
            latest_draft,
            (latest_draft.c.tid == Thread.id) & (latest_draft.c.rn == 1),
        )
        .where(
            Thread.mailbox.in_(mailbox_emails),
            needs_elise_action(latest_draft),
        )
        .subquery()
    )

    stmt = (
        select(
            Thread,
            msg_count,
            latest_msg.c.sender,
            latest_msg.c.direction,
            latest_msg.c.to_recipients,
            latest_msg.c.body_preview,
            latest_msg.c.body_text,
            latest_msg.c.unique_body_text,
            latest_msg.c.graph_message_id,
        )
        .join(ranked, ranked.c.tid == Thread.id)
        .outerjoin(Message, Message.thread_id == Thread.id)
        .outerjoin(
            latest_msg,
            (latest_msg.c.tid == Thread.id) & (latest_msg.c.rn == 1),
        )
        .where(ranked.c.rn <= per_mailbox)
        .group_by(
            Thread.id,
            latest_msg.c.sender,
            latest_msg.c.direction,
            latest_msg.c.to_recipients,
            latest_msg.c.body_preview,
            latest_msg.c.body_text,
            latest_msg.c.unique_body_text,
            latest_msg.c.graph_message_id,
        )
        .order_by(Thread.mailbox, Thread.last_message_at.desc().nullslast(), Thread.id.desc())
    )
    result = await session.execute(stmt)
    rows = result.all()

    buckets: dict[str, list[ThreadSummary]] = {email.lower(): [] for email in mailbox_emails}
    pairs: list[tuple[str, str]] = []
    thread_ids: list[uuid.UUID] = []
    pending: list[tuple[str, uuid.UUID, str]] = []
    for (
        thread,
        count,
        sender,
        direction,
        to_recipients,
        body_preview,
        body_text,
        unique_body_text,
        graph_message_id,
    ) in rows:
        key = thread.mailbox.lower()
        if key not in buckets or len(buckets[key]) >= per_mailbox:
            continue
        pairs.append((thread.mailbox, thread.conversation_id))
        thread_ids.append(thread.id)
        party = party_sender(thread.mailbox, sender, direction, to_recipients)
        summary = ThreadSummary(
            id=thread.id,
            mailbox=thread.mailbox,
            mailbox_key=infer_mailbox_key(thread.mailbox),
            subject=thread.subject,
            state=display_state(thread.state, mailbox=thread.mailbox, last_sender=party),
            urgency=thread.urgency,
            urgency_reason=thread.urgency_reason,
            category=thread.category,
            last_message_at=thread.last_message_at,
            last_sender=party,
            preview=card_preview(body_text, unique_body_text, body_preview),
            staleness_hours=thread_repo._staleness_hours(thread.last_message_at, now),
            message_count=int(count or 0),
            outlook_url=outlook_web_link(graph_message_id) if graph_message_id else None,
        )
        buckets[key].append(summary)
        pending.append((thread.mailbox, thread.id, thread.conversation_id))

    await _enrich_preview_buckets(
        session,
        buckets=buckets,
        pending=pending,
        pairs=pairs,
        thread_ids=thread_ids,
    )
    return {email: buckets.get(email.lower(), []) for email in mailbox_emails}
