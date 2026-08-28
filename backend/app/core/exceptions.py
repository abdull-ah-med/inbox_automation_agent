from __future__ import annotations

import uuid


class InboxTriageError(Exception):
    """Base exception for inbox triage domain errors."""


class ClassificationError(InboxTriageError):
    """Raised when email classification fails."""


class TriageError(InboxTriageError):
    """Raised when Haiku triage parse/API fails after retry."""


class RuleEngineError(InboxTriageError):
    """Raised when rule engine evaluation fails."""


class GraphClientError(InboxTriageError):
    """Raised when Microsoft Graph API calls fail."""


class DraftGenerationError(InboxTriageError):
    """Raised when draft generation fails."""


class ThreadStateError(InboxTriageError):
    """Raised when thread state transitions are invalid."""


class AuditError(InboxTriageError):
    """Raised when audit logging fails."""


class AuthError(InboxTriageError):
    """Base exception for authentication failures."""


class InvalidCredentialsError(AuthError):
    """Raised when email/password login fails."""


class InvalidTokenError(AuthError):
    """Raised when a refresh or access token is invalid or expired."""


class ReusedRefreshTokenError(AuthError):
    """Raised when a revoked refresh token is presented (theft signal)."""


class InvalidCursorError(InboxTriageError):
    """Raised when a pagination cursor is malformed."""


class DraftNotFoundError(InboxTriageError):
    """Raised when a draft id does not exist."""


class SkillNotFoundError(InboxTriageError):
    """Raised when a skill id does not exist."""


class ReplyMemoryNotFoundError(InboxTriageError):
    """Raised when a reply-memory (tone reference) id does not exist."""


class RejectionMemoryNotFoundError(InboxTriageError):
    """Raised when a rejection-memory id does not exist."""


class SkillNameConflictError(InboxTriageError):
    """Raised when creating/updating a skill with a duplicate name."""


class SkillBudgetExceededError(InboxTriageError):
    """Raised when activating a skill would exceed active count or char budget."""


class SkillArchiveError(InboxTriageError):
    """Base error for Claude skill archive import failures."""


class SkillPackagingError(SkillArchiveError):
    """Raised when a skill zip does not follow Anthropic packaging rules."""


class SkillPathTraversalError(SkillArchiveError):
    """Raised when a skill archive contains unsafe paths."""


class SkillPackageTooLargeError(SkillArchiveError):
    """Raised when archive or uncompressed sizes exceed safety limits."""


class SkillAlreadyImportedError(SkillArchiveError):
    """Raised when the same archive hash was already imported without overwrite."""

    def __init__(self, message: str, *, skill_id: uuid.UUID) -> None:
        super().__init__(message)
        self.skill_id = skill_id


class SkillDuplicateCandidatesError(SkillArchiveError):
    """Raised when import finds semantically similar existing skills."""

    def __init__(
        self,
        message: str = "Similar skills already exist",
        *,
        candidates: list[dict[str, object]],
    ) -> None:
        super().__init__(message)
        self.candidates = candidates


class ThreadNotFoundError(InboxTriageError):
    """Raised when a thread id does not exist for regeneration or lookup."""


class InvalidDateRangeError(InboxTriageError):
    """Raised when an ops-metrics date window is inverted or too wide."""


class UnknownMailboxError(InboxTriageError):
    """Raised when a mailbox filter is not in the configured allowlist."""


class EmptySearchQueryError(InboxTriageError):
    """Raised when a search query is missing or blank after strip."""


class SearchError(InboxTriageError):
    """Raised when hybrid retrieval cannot complete either search leg."""


class ChatError(InboxTriageError):
    """Raised when the grounded chat answer call fails after retry."""
