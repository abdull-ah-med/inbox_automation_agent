"""Whether Sonnet should write a letter. Teaching notes always run except spam.

Letter is off for automated mail and RSVP-only calendar invites. Letter is
on when a meetingRequest (or other calendar message) includes a personal ask
beyond time/place/join-link chrome.
"""

from __future__ import annotations

import re

from app.models.schemas.classification import TriageResultSchema
from app.models.schemas.email import EmailMessageSchema

_MEETING_TYPES = frozenset(
    {
        "meetingRequest",
        "meetingCancelled",
        "meetingAccepted",
        "meetingTenativelyAccepted",
        "meetingDeclined",
    }
)
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"\b\S+@\S+\.\S+\b")
_LABEL_LINE_RE = re.compile(
    r"^\s*(?:when|where|who|organizer|guests?|location|what)\s*:.*$",
    re.IGNORECASE | re.MULTILINE,
)
_TIME_RE = re.compile(
    r"\b(?:\d{1,2}:\d{2}\s*(?:[ap]m)?|"
    r"(?:eastern|pacific|central|mountain)\s*time|"
    r"edt|est|pdt|pst|cdt|cst|mdt|mst)\b",
    re.IGNORECASE,
)
_ASK_CUE_RE = re.compile(
    r"\b(?:please|pls|can you|could you|would you|can we|could we|"
    r"need you|want you to|let me know)\b|\?",
    re.IGNORECASE,
)
_GREETING_RE = re.compile(
    r"\b(?:hi|hello|hey|dear|good (?:morning|afternoon|evening))\b",
    re.IGNORECASE,
)
_BOILERPLATE_PHRASES = (
    "join with google meet",
    "join with microsoft teams",
    "microsoft teams meeting",
    "join zoom meeting",
    "hanging out with google meet",
    "view event details",
    "google calendar",
    "outlook calendar",
    "has invited you",
    "you've been invited",
    "you have been invited",
    "invited you to",
    "invitation from",
)
_WORD_RE = re.compile(r"[a-z]{2,}")


def _strip_invite_chrome(body: str) -> str:
    lowered = _URL_RE.sub(" ", body)
    lowered = _EMAIL_RE.sub(" ", lowered)
    lowered = _LABEL_LINE_RE.sub(" ", lowered)
    lowered = lowered.lower()
    for phrase in _BOILERPLATE_PHRASES:
        lowered = lowered.replace(phrase, " ")
    return _TIME_RE.sub(" ", lowered)


def invite_has_personal_message(body: str | None) -> bool:
    """True when calendar mail includes a written ask, not just RSVP chrome."""
    raw = (body or "").strip()
    if not raw:
        return False
    if _ASK_CUE_RE.search(raw):
        return True
    stripped = _strip_invite_chrome(raw)
    if _ASK_CUE_RE.search(stripped):
        return True
    words = _WORD_RE.findall(stripped)
    return bool(_GREETING_RE.search(stripped) and len(words) >= 6)


def _invite_body(email: EmailMessageSchema) -> str:
    if email.unique_body_text is not None and email.unique_body_text.strip():
        return email.unique_body_text
    for candidate in (email.body_clean, email.body_text, email.body_preview):
        if candidate and str(candidate).strip():
            return str(candidate)
    return ""


def resolve_draft_needed(
    *,
    email: EmailMessageSchema,
    triage: TriageResultSchema,
) -> bool:
    """Return whether Sonnet should emit a reply_body."""
    if triage.is_spam:
        return False
    if email.is_automated:
        return False
    meeting = (email.meeting_message_type or "").strip()
    if meeting in _MEETING_TYPES:
        return invite_has_personal_message(_invite_body(email))
    if not triage.has_action_items:
        return False
    return bool(triage.draft_needed)
