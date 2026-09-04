"""Stable, ID-free public error messages for API clients.

Internal exception strings may contain UUIDs, Graph paths, or secrets.
Map each known ``InboxTriageError`` subclass to a fixed phrase; keep
``error_type`` (class name) for clients that branch on it.
"""

from __future__ import annotations

from app.core.exceptions import (
    AuthError,
    DraftNotFoundError,
    EmptySearchQueryError,
    GraphClientError,
    InboxTriageError,
    InvalidCredentialsError,
    InvalidCursorError,
    InvalidDateRangeError,
    InvalidTokenError,
    ProposalConflictError,
    ProposalNotFoundError,
    RejectionMemoryNotFoundError,
    ReplyMemoryNotFoundError,
    ReusedRefreshTokenError,
    SkillAlreadyImportedError,
    SkillArchiveError,
    SkillBudgetExceededError,
    SkillDuplicateCandidatesError,
    SkillNameConflictError,
    SkillNotFoundError,
    SkillPackageTooLargeError,
    SkillPackagingError,
    SkillPathTraversalError,
    ThreadContextVersionConflict,
    ThreadNotFoundError,
    ThreadStateError,
    UnknownMailboxError,
)

_PUBLIC_DETAIL: dict[type[BaseException], str] = {
    GraphClientError: "Upstream Microsoft Graph request failed",
    InvalidCredentialsError: "Invalid credentials",
    InvalidTokenError: "Invalid or expired token",
    ReusedRefreshTokenError: "Authentication failed",
    AuthError: "Authentication failed",
    DraftNotFoundError: "Draft not found",
    ThreadNotFoundError: "Thread not found",
    SkillNotFoundError: "Skill not found",
    ReplyMemoryNotFoundError: "Reply memory not found",
    RejectionMemoryNotFoundError: "Rejection memory not found",
    InvalidCursorError: "Invalid cursor",
    InvalidDateRangeError: "Invalid date range",
    EmptySearchQueryError: "Empty search query",
    UnknownMailboxError: "Mailbox not found",
    ProposalNotFoundError: "Proposal not found",
    ProposalConflictError: "Proposal is not in the required state",
    SkillNameConflictError: "Skill name already exists",
    SkillBudgetExceededError: "Skill budget exceeded",
    ThreadStateError: "Invalid thread state",
    ThreadContextVersionConflict: "Thread context was updated elsewhere — reload and try again",
    SkillPathTraversalError: "Invalid skill archive",
    SkillPackageTooLargeError: "Skill archive too large",
    SkillPackagingError: "Invalid skill package",
    SkillAlreadyImportedError: "Skill already imported",
    SkillDuplicateCandidatesError: "Similar skills already exist",
    SkillArchiveError: "Skill archive error",
}


def public_detail(exc: BaseException) -> str:
    """Return a stable public ``detail`` string for ``exc`` (never raw ``str(exc)``)."""
    for cls, message in _PUBLIC_DETAIL.items():
        if isinstance(exc, cls):
            return message
    if isinstance(exc, InboxTriageError):
        return "Request failed"
    return "Request failed"
