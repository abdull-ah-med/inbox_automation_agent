from datetime import datetime

from pydantic import BaseModel, Field

from app.models.schemas.classification import ClassificationResultSchema
from app.models.schemas.draft import DraftSchema
from app.models.schemas.email import ThreadContextSchema


class SlackActionSchema(BaseModel):
    action_id: str
    user_id: str
    thread_id: str
    conversation_id: str | None = None


class ReviewCardDataSchema(BaseModel):
    mailbox: str
    received_at: datetime
    thread: ThreadContextSchema
    classification_result: ClassificationResultSchema
    draft: DraftSchema | None = None
    thread_depth: int = Field(ge=0)
