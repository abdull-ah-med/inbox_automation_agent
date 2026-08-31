"""Mailbox allowlist that repositories must receive on tenant-scoped reads."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.core.config import Settings, get_settings


@dataclass(frozen=True)
class TenantScope:
    """Mandatory mailbox context for ``get_by_id`` and similar repo reads."""

    mailboxes: tuple[str, ...]

    def __post_init__(self) -> None:
        cleaned = tuple(
            item.strip().lower()
            for item in self.mailboxes
            if isinstance(item, str) and item.strip()
        )
        if not cleaned:
            raise ValueError("TenantScope requires at least one mailbox")
        object.__setattr__(self, "mailboxes", cleaned)

    @classmethod
    def single(cls, mailbox: str) -> TenantScope:
        return cls(mailboxes=(mailbox,))

    @classmethod
    def allowlist(cls, emails: Sequence[str]) -> TenantScope:
        return cls(mailboxes=tuple(emails))

    @classmethod
    def from_settings(cls, settings: Settings) -> TenantScope:
        return cls.allowlist(settings.mailbox_list)

    @classmethod
    def for_request(cls, settings: Settings | None) -> TenantScope:
        if settings is not None:
            return cls.from_settings(settings)
        return cls.from_settings(get_settings())

    def allows(self, mailbox: str) -> bool:
        return mailbox.strip().lower() in self.mailboxes
