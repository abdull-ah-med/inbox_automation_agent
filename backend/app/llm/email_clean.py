"""Strip quotes, legal disclaimers, and signatures from email bodies.

CLEAN_VERSION bumps whenever heuristics change (Talon pin, disclaimer list,
ordering). Persist alongside ``body_clean`` so stale rows are detectable.
"""

from __future__ import annotations

import re

import structlog
from pydantic import BaseModel, ConfigDict, Field

from app.llm._vendor.talon import quotations as talon_quotations
from app.llm._vendor.talon import utils as talon_utils
from app.llm._vendor.talon.signature import bruteforce as talon_bruteforce

logger = structlog.get_logger(__name__)

# Bump when cleaning heuristics change so backfills can find stale rows.
CLEAN_VERSION: int = 2

_DISCLAIMER_TRIGGERS: tuple[str, ...] = (
    "this email and any files transmitted with it",
    "this e-mail and any files transmitted with it",
    "this message contains confidential information",
    "confidentiality notice",
    "privileged and confidential",
    "intended solely for the use of the individual or entity",
    "if you have received this email in error",
    "if you are not the intended recipient",
    "disclaimer:",
)
_DISCLAIMER_RE = re.compile(
    "|".join(re.escape(p) for p in _DISCLAIMER_TRIGGERS),
    re.IGNORECASE,
)
_DISCLAIMER_TAIL_CHARS = 2_000
_DISCLAIMER_TAIL_LINES = 40
_EXCESS_BLANK_RE = re.compile(r"\n{3,}")

# Zendesk / ticket delimiter and Graph Prefer-text artifacts.
_REPLY_DELIMITER_RE = re.compile(
    r"(?im)^\s*(?:##-\s*)?Please type your reply above this line(?:\s*-##)?\s*$"
    r"|^\s*--\s*reply above this line\s*--\s*$"
)
_CID_TOKEN_RE = re.compile(r"\[cid:[^\]]+\]", re.IGNORECASE)
_IMAGE_URL_PLACEHOLDER_RE = re.compile(
    r"\[https?://[^\]]+\.(?:png|jpe?g|gif|webp|svg)(?:\?[^\]]*)?\]",
    re.IGNORECASE,
)
# Nested mailto / angle-bracket link junk from Graph text conversion.
_MAILTO_ANGLE_RE = re.compile(r"<mailto:[^<>]*(?:<[^>]*>[^<>]*)*>", re.IGNORECASE)
_MAILTO_SIMPLE_RE = re.compile(r"<mailto:[^>]+>", re.IGNORECASE)
_ANGLE_URL_RE = re.compile(
    r"(?P<label>[^\s<>]+)\s*<(?P<url>https?://[^>]+)>",
    re.IGNORECASE,
)


def strip_plain_text_artifacts(text: str) -> str:
    """Remove Zendesk delimiters, cid/image placeholders, and nested mailto junk."""
    if not text:
        return text
    out = _normalize_newlines(text)
    out = "\n".join(line for line in out.split("\n") if not _REPLY_DELIMITER_RE.match(line))
    out = _CID_TOKEN_RE.sub("", out)
    out = _IMAGE_URL_PLACEHOLDER_RE.sub("", out)

    # Peel nested mailto angle clusters (Graph Prefer-text duplication).
    for _ in range(6):
        nxt = _MAILTO_ANGLE_RE.sub("", out)
        nxt = _MAILTO_SIMPLE_RE.sub("", nxt)
        if nxt == out:
            break
        out = nxt

    def _url_repl(match: re.Match[str]) -> str:
        label = match.group("label").strip()
        url = match.group("url").strip()
        if label.lower() in {"web", "w", "link"} or label.startswith("http"):
            return f"{label} {url}".strip() if label.lower() in {"web", "w"} else url
        return label

    out = _ANGLE_URL_RE.sub(_url_repl, out)
    # Drop leftover angle brackets from broken nested mailto expansions.
    out = re.sub(r"(?<=\S)>+\s*$", "", out, flags=re.MULTILINE)
    out = re.sub(r"^\s*<+(?=\S)", "", out, flags=re.MULTILINE)
    return _collapse_blank_lines(out)


class CleanedEmailBody(BaseModel):
    model_config = ConfigDict(frozen=True)

    body_clean: str
    quote_stripped: bool = False
    signature_stripped: bool = False
    disclaimer_stripped: bool = False
    clean_version: int = Field(default=CLEAN_VERSION)


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _collapse_blank_lines(text: str) -> str:
    return _EXCESS_BLANK_RE.sub("\n\n", text).strip()


def _strip_legal_disclaimer(text: str) -> tuple[str, bool]:
    """Truncate trailing corporate/legal boilerplate.

    Only searches the last ~40 lines / 2k chars to avoid mid-body false
    positives (e.g. a legitimate discussion of confidentiality).
    """
    if not text.strip():
        return text, False

    lines = text.split("\n")
    if len(lines) > _DISCLAIMER_TAIL_LINES:
        head = "\n".join(lines[:-_DISCLAIMER_TAIL_LINES])
        tail = "\n".join(lines[-_DISCLAIMER_TAIL_LINES:])
    else:
        head = ""
        tail = text

    if len(tail) > _DISCLAIMER_TAIL_CHARS:
        # Keep the search window at the end of the already-bounded tail.
        overflow = len(tail) - _DISCLAIMER_TAIL_CHARS
        head = (head + "\n" + tail[:overflow]).lstrip("\n") if head else tail[:overflow]
        tail = tail[overflow:]

    match = _DISCLAIMER_RE.search(tail)
    if match is None:
        return text, False

    # Truncate from the start of the matched line.
    line_start = tail.rfind("\n", 0, match.start()) + 1
    stripped_tail = tail[:line_start].rstrip()
    if head:
        result = f"{head}\n{stripped_tail}".rstrip() if stripped_tail else head.rstrip()
    else:
        result = stripped_tail
    return result, True


def _strip_quotes(text: str, *, content_type: str) -> tuple[str, bool]:
    if content_type == "html":
        html_reply = talon_quotations.extract_from_html(text)
        if not html_reply:
            plain = talon_utils.html_to_text(text) or text
            return plain.strip(), False
        plain = talon_utils.html_to_text(html_reply) or ""
        stripped = plain.strip()
        # Compare against HTML→text of the original so we detect real cuts.
        original_plain = (talon_utils.html_to_text(text) or text).strip()
        return stripped, stripped != original_plain and bool(stripped or original_plain)

    extracted = talon_quotations.extract_from_plain(text)
    stripped = (extracted or text).strip()
    return stripped, stripped != text.strip()


def _strip_signature(text: str) -> tuple[str, bool]:
    body, signature = talon_bruteforce.extract_signature(text)
    if signature:
        return (body or "").strip(), True
    return text.strip(), False


def to_plain_text(raw_body: str, *, content_type: str = "text") -> str:
    """Return Graph body/uniqueBody content as plain text.

    Whitespace-only input is empty. HTML is converted; already-plain text is
    returned as-is so internal newlines in a real reply are kept.
    """
    if not (raw_body or "").strip():
        return ""
    ctype = (content_type or "text").strip().lower()
    if ctype == "html":
        return (talon_utils.html_to_text(raw_body) or "").strip()
    return raw_body


def _fallback_plain(raw_body: str, *, content_type: str) -> str:
    normalized = _normalize_newlines(raw_body or "")
    if content_type == "html":
        return (talon_utils.html_to_text(normalized) or normalized).strip()
    return normalized.strip()


def clean_email_body(raw_body: str, *, content_type: str = "text") -> CleanedEmailBody:
    """Return cleaned plain text; never raises into ingest."""
    ctype = (content_type or "text").strip().lower()
    if ctype not in {"text", "html"}:
        logger.warning("email_clean_unknown_content_type", content_type=ctype)
        ctype = "text"

    try:
        normalized = _normalize_newlines(raw_body or "")
        if not normalized.strip():
            return CleanedEmailBody(body_clean="")

        after_quotes, quote_stripped = _strip_quotes(normalized, content_type=ctype)
        after_artifacts = strip_plain_text_artifacts(after_quotes)
        after_disclaimer, disclaimer_stripped = _strip_legal_disclaimer(after_artifacts)
        after_signature, signature_stripped = _strip_signature(after_disclaimer)
        body_clean = _collapse_blank_lines(after_signature)

        return CleanedEmailBody(
            body_clean=body_clean,
            quote_stripped=quote_stripped,
            signature_stripped=signature_stripped,
            disclaimer_stripped=disclaimer_stripped,
        )
    except Exception:
        logger.exception("email_clean_failed", content_type=ctype)
        return CleanedEmailBody(
            body_clean=_fallback_plain(raw_body or "", content_type=ctype),
            quote_stripped=False,
            signature_stripped=False,
            disclaimer_stripped=False,
        )


def effective_body_text(
    *,
    body_clean: str | None,
    body_text: str,
    body_content_type: str = "text",
) -> str:
    """Prefer stored ``body_clean``; recompute if missing/empty."""
    if body_clean and body_clean.strip():
        return body_clean
    return clean_email_body(body_text, content_type=body_content_type).body_clean
