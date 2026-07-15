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
