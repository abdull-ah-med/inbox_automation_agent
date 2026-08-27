"""Deterministic PII scrubbing for LLM egress only.

Full messages stay in Postgres for human review. This module returns *copies*
with identifiers masked before any Anthropic call. No DB, Redis, or HTTP.
"""

from __future__ import annotations

import re

from app.models.schemas.email import EmailMessageSchema, ThreadContextSchema

TOKEN_SSN = "[REDACTED_SSN]"
TOKEN_DOB = "[REDACTED_DOB]"
TOKEN_DL = "[REDACTED_DL]"
TOKEN_BANK = "[REDACTED_BANK]"
TOKEN_CARD = "[REDACTED_CARD]"
TOKEN_ID = "[REDACTED_ID]"
TOKEN_PHONE = "[REDACTED_PHONE]"

# Ordered: more specific / higher-confidence patterns first.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # SSN with separators (XXX-XX-XXXX or XXX XX XXXX)
    (
        re.compile(r"(?<!\d)(?!000|666|9\d{2})\d{3}[-\s](?!00)\d{2}[-\s](?!0000)\d{4}(?!\d)"),
        TOKEN_SSN,
    ),
    # Labeled SSN / SS# → 9 digits (with or without separators)
    (
        re.compile(
            r"(?i)\b(?:ssn|ss\s*#|social\s+security(?:\s+number)?)\s*[:#]?\s*"
            r"(?:\d{3}[-\s]?\d{2}[-\s]?\d{4})"
        ),
        TOKEN_SSN,
    ),
    # Labeled DoB / date of birth / born on → common date forms
    (
        re.compile(
            r"(?i)\b(?:dob|d\.o\.b\.|date\s+of\s+birth|born\s+on|birth\s+date)\s*[:#]?\s*"
            r"(?:"
            r"\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}"
            r"|"
            r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
            r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
            r"Dec(?:ember)?)\s+\d{1,2},?\s+\d{2,4}"
            r")"
        ),
        TOKEN_DOB,
    ),
    # Labeled driver / CDL / license number
    (
        re.compile(
            r"(?i)\b(?:driver'?s?\s+license|dl|cdl|license(?:\s+(?:number|no\.?))?)"
            r"\s*[#:]+\s*"
            r"[A-Z0-9][A-Z0-9\-]{4,20}"
        ),
        TOKEN_DL,
    ),
    # IBAN
    (
        re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),
        TOKEN_BANK,
    ),
    # Labeled routing / ABA
    (
        re.compile(
            r"(?i)\b(?:routing(?:\s+(?:number|no\.?|#))?|aba)\s*[:#]?\s*"
            r"\d{9}\b"
        ),
        TOKEN_BANK,
    ),
    # Labeled bank / account number (6-17 digits)
    (
        re.compile(
            r"(?i)\b(?:bank\s+)?(?:account|acct)(?:\s+(?:number|no\.?|#))?\s*[:#]?\s*"
            r"\d{6,17}\b"
        ),
        TOKEN_BANK,
    ),
    # Labeled passport
    (
        re.compile(
            r"(?i)\bpassport(?:\s+(?:number|no\.?|#))?\s*[:#]?\s*"
            r"[A-Z0-9]{6,12}\b"
        ),
        TOKEN_ID,
    ),
    # Labeled EIN (XX-XXXXXXX)
    (
        re.compile(
            r"(?i)\b(?:ein|employer\s+identification(?:\s+number)?)\s*[:#]?\s*"
            r"\d{2}-?\d{7}\b"
        ),
        TOKEN_ID,
    ),
    # Labeled phone (NIST SP 800-122 treats telephone numbers as PII).
    (
        re.compile(
            r"(?i)\b(?:phone|tel(?:ephone)?|mobile|cell|fax)\s*(?:number|no\.?|#)?"
            r"\s*[:#]?\s*"
            r"(?:"
            r"\+?\d{1,3}[\s./-]?)?"
            r"\(?\d{2,4}\)?[\s./-]?\d{2,4}[\s./-]?\d{3,4}"
        ),
        TOKEN_PHONE,
    ),
    # E.164-style international (+ and country code; ITU-T E.164 max 15 digits).
    (
        re.compile(r"(?<!\w)\+[1-9]\d{6,14}(?!\d)"),
        TOKEN_PHONE,
    ),
    # NANP with separators: (415) 555-0123 / 415-555-0123 / 415.555.0123
    # Area/central office NXX cannot start with 0 or 1 (NANP).
    (
        re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?[2-9]\d{2}\)?[\s.-][2-9]\d{2}[\s.-]\d{4}(?!\d)"),
        TOKEN_PHONE,
    ),
)

# Payment-card candidates: 13-19 digits with optional separators; validated by Luhn.
_CARD_CANDIDATE = re.compile(r"(?<!\d)(?:\d[ -]*?){13,19}(?!\d)")


def _luhn_ok(digits: str) -> bool:
    if not digits.isdigit() or not 13 <= len(digits) <= 19:
        return False
    total = 0
    reverse = digits[::-1]
    for i, ch in enumerate(reverse):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _scrub_cards(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        if _luhn_ok(digits):
            return TOKEN_CARD
        return match.group(0)

    return _CARD_CANDIDATE.sub(repl, text)


def scrub_text(text: str) -> str:
    """Mask high-risk identifiers in free text for LLM egress."""
    if not text:
        return text
    result = text
    for pattern, token in _PATTERNS:
        result = pattern.sub(token, result)
    return _scrub_cards(result)


def scrub_email_for_llm(email: EmailMessageSchema) -> EmailMessageSchema:
    """Return a copy with subject/body scrubbed; recipients and ids unchanged."""
    return email.model_copy(
        update={
            "subject": scrub_text(email.subject),
            "body_text": scrub_text(email.body_text),
            "body_preview": (
                scrub_text(email.body_preview) if email.body_preview is not None else None
            ),
            "body_clean": (scrub_text(email.body_clean) if email.body_clean is not None else None),
            "summary_one_line": (
                scrub_text(email.summary_one_line) if email.summary_one_line is not None else None
            ),
        }
    )


def scrub_thread_for_llm(thread: ThreadContextSchema) -> ThreadContextSchema:
    """Return a copy with subject and each message scrubbed for LLM egress."""
    scrubbed_messages = [scrub_email_for_llm(msg) for msg in thread.messages]
    return thread.model_copy(
        update={
            "subject": scrub_text(thread.subject),
            "messages": scrubbed_messages,
        }
    )
