"""Outlook Web deep-link helpers (read-only open targets)."""

from __future__ import annotations

from urllib.parse import quote


def outlook_web_link(message_id: str) -> str:
    """OWA deep link for a Graph message id (matches Graph ``message.webLink`` shape)."""
    encoded = quote(message_id, safe="")
    return (
        f"https://outlook.office365.com/owa/?ItemID={encoded}&exvsurl=1&viewmodel=ReadMessageItem"
    )
