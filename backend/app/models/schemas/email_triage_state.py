from typing import Literal

from pydantic import BaseModel, Field

from app.models.schemas.classification import ClassificationResultSchema, TriageResultSchema
from app.models.schemas.draft import DraftSchema
from app.models.schemas.email import EmailMessageSchema, ThreadContextSchema

DraftStatus = Literal["PENDING", "DRAFTED", "SKIPPED", "REQUIRES_HUMAN"]

# Slack review-card delivery relative to ingest dedup completion.
# ``not_required`` = simulate / no-card paths; ``skipped_unconfigured`` = local without Slack.
SlackDelivery = Literal[
    "not_attempted",
    "not_required",
    "posted",
    "already_posted",
    "skipped_unconfigured",
    "failed",
]


class CrossThreadContextSchema(BaseModel):
    matched_conversation_id: str
    similarity_score: float = Field(ge=0.0, le=1.0)
    thread_messages: list[EmailMessageSchema] = Field(default_factory=list)


class EmailTriageState(BaseModel):
    original_email: EmailMessageSchema
    thread_context: ThreadContextSchema
    cross_thread_context: CrossThreadContextSchema | None = None
    triage: TriageResultSchema | None = None
    classification: ClassificationResultSchema | None = None
    matched_rule: str | None = None
    draft: DraftSchema | None = None
    draft_status: DraftStatus = "PENDING"
    slack_delivery: SlackDelivery = "not_attempted"
    error_logs: list[str] = Field(default_factory=list)
