"""Schemas for applying a salutation rewrite to an existing draft."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.schemas.dashboard import DraftView, ReplyAddresseeView
from app.models.schemas.mailbox_contact import MailboxContactView


class DraftSalutationApplySchema(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    first_name: str = Field(min_length=1, max_length=100)
    full_name: str = Field(default="", max_length=200)
    notes: str | None = Field(default=None, max_length=2000)


class DraftSalutationApplyResponse(BaseModel):
    draft: DraftView
    reply_addressee: ReplyAddresseeView
    contact: MailboxContactView
