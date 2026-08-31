"""Apply a taught salutation to an existing draft without LLM regeneration."""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.draft_salutation import rewrite_opening_salutation
from app.core.exceptions import DraftNotFoundError
from app.core.tenant_scope import TenantScope
from app.models.schemas.dashboard import DraftView, ReplyAddresseeView
from app.models.schemas.mailbox_contact import MailboxContactView
from app.repositories import draft_repo, thread_repo
from app.services import mailbox_contact_service, thread_view_service


async def apply_draft_salutation(
    session: AsyncSession,
    settings: Settings,
    draft_id: uuid.UUID,
    *,
    email: str,
    first_name: str,
    full_name: str = "",
    notes: str | None = None,
    actor_user_id: uuid.UUID | None = None,
) -> tuple[DraftView, ReplyAddresseeView, MailboxContactView]:
    """Upsert the mailbox contact and rewrite the draft opening greeting.

    Persists the new body on ``edited_body`` so the reviewer sees the change
    immediately without a regenerate / LLM call.
    """
    draft = await draft_repo.get_draft_by_id(session, draft_id)
    if draft is None:
        raise DraftNotFoundError(f"Draft not found: {draft_id}")

    thread = await thread_repo.get_by_id(
        session, draft.thread_id, TenantScope.for_request(settings)
    )
    if thread is None or not settings.mailbox_allowed(thread.mailbox):
        raise DraftNotFoundError(f"Draft not found: {draft_id}")

    trimmed_first = (first_name or "").strip()
    if not trimmed_first:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="first_name is required",
        )

    contact, _created = await mailbox_contact_service.upsert_contact(
        session,
        settings,
        mailbox_key=thread.mailbox,
        email=email,
        full_name=(full_name or "").strip() or trimmed_first,
        first_name=trimmed_first,
        notes=notes,
        actor_user_id=actor_user_id,
    )

    current_body = draft.edited_body or draft.reply_body
    new_body = rewrite_opening_salutation(current_body, trimmed_first)
    updated = await draft_repo.set_edited_body(session, draft_id, edited_body=new_body)
    if updated is None:
        raise DraftNotFoundError(f"Draft not found: {draft_id}")

    draft_view = thread_view_service.draft_response_to_view(updated)
    addressee = ReplyAddresseeView(
        email=contact.email,
        salute_name=contact.first_name,
        source="directory",
        source_kind="directory",
        directory_hit=True,
    )
    return draft_view, addressee, contact
