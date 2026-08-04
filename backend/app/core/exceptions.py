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


class SkillNameConflictError(InboxTriageError):
    """Raised when creating/updating a skill with a duplicate name."""


class SkillBudgetExceededError(InboxTriageError):
    """Raised when activating a skill would exceed active count or char budget."""


class ThreadNotFoundError(InboxTriageError):
    """Raised when a thread id does not exist for regeneration or lookup."""
