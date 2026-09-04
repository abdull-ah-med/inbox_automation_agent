"""Pydantic schemas for teaching-note API endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Scope ladder order for widening proposals (narrower → wider).
_SCOPE_LADDER = (
    "thread",
    "sender_address",
    "sender_domain",
    "mailbox+routing_category",
    "mailbox",
    "global",
)

TeachingNoteCreateScope = Literal[
    "sender_address",
    "sender_domain",
    "mailbox+routing_category",
    "mailbox",
]
TeachingNotePatchScope = Literal[
    "thread",
    "sender_address",
    "sender_domain",
    "mailbox+routing_category",
    "mailbox",
]


def _scope_index(scope: str) -> int:
    try:
        return _SCOPE_LADDER.index(scope)
    except ValueError:
        return len(_SCOPE_LADDER)


def scope_is_wider(new_scope: str, current_scope: str) -> bool:
    """Return True when *new_scope* is strictly wider than *current_scope*."""
    return _scope_index(new_scope) > _scope_index(current_scope)


class TeachingNoteResponseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    mailbox: str
    title: str
    body: str
    applies_when: str | None = None
    scope: str
    scope_key: str
    status: str
    origin: str
    origin_atom_id: uuid.UUID | None = None
    person_bound: bool
    hit_count: int
    precision_num: int
    precision_den: int
    created_at: datetime | None = None
    updated_at: datetime | None = None
    proposal_id: uuid.UUID | None = None


class CreateTeachingNoteSchema(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    mailbox: str = Field(min_length=1, max_length=320)
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=2_000)
    applies_when: str | None = Field(default=None, max_length=1_000)
    scope: TeachingNoteCreateScope
    sender_address: str | None = Field(default=None, max_length=320)
    sender_domain: str | None = Field(default=None, max_length=255)
    routing_category: str | None = Field(default=None, max_length=32)
    person_bound: bool = False


class UpdateTeachingNoteSchema(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    body: str | None = Field(default=None, min_length=1, max_length=2_000)
    applies_when: str | None = Field(default=None, max_length=1_000)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    scope: TeachingNotePatchScope | None = None
    sender_address: str | None = Field(default=None, max_length=320)
    sender_domain: str | None = Field(default=None, max_length=255)
    routing_category: str | None = Field(default=None, max_length=32)
