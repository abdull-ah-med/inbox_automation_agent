from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.schemas.classification import TriageResultSchema
from app.models.schemas.draft import DraftSchema
from app.models.schemas.email import ThreadContextSchema
from app.models.schemas.email_triage_state import DraftStatus


class GraphNotificationResourceDataSchema(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    odata_type: str | None = Field(default=None, alias="@odata.type")
    odata_id: str | None = Field(default=None, alias="@odata.id")
    odata_etag: str | None = Field(default=None, alias="@odata.etag")
    id: str | None = None


class GraphNotificationItemSchema(BaseModel):
    """Change or lifecycle notification item.

    Per Graph docs, changeType and lifecycleEvent are mutually exclusive.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str | None = None
    subscription_id: str = Field(alias="subscriptionId")
    client_state: str | None = Field(default=None, alias="clientState")
    change_type: Literal["created", "updated", "deleted"] | None = Field(
        default=None,
        alias="changeType",
    )
    lifecycle_event: Literal["missed", "subscriptionRemoved", "reauthorizationRequired"] | None = (
        Field(default=None, alias="lifecycleEvent")
    )
    resource: str
    subscription_expiration_date_time: datetime | None = Field(
        default=None,
        alias="subscriptionExpirationDateTime",
    )
    tenant_id: str | None = Field(default=None, alias="tenantId")
    resource_data: GraphNotificationResourceDataSchema | None = Field(
        default=None,
        alias="resourceData",
    )

    @model_validator(mode="after")
    def require_change_or_lifecycle(self) -> "GraphNotificationItemSchema":
        if self.change_type is None and self.lifecycle_event is None:
            raise ValueError("Either changeType or lifecycleEvent is required")
        return self


class GraphNotificationSchema(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    value: list[GraphNotificationItemSchema] = Field(default_factory=list)


class GraphValidationSchema(BaseModel):
    validation_token: str


class GraphEmailAddressSchema(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    name: str | None = None
    address: str | None = None


class GraphRecipientSchema(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    email_address: GraphEmailAddressSchema | None = Field(default=None, alias="emailAddress")


class GraphMessageBodySchema(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    content_type: str | None = Field(default=None, alias="contentType")
    content: str | None = None


class GraphMessageSchema(BaseModel):
    """Subset of Microsoft Graph message resource used by ingestion."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str
    subject: str | None = None
    body_preview: str | None = Field(default=None, alias="bodyPreview")
    body: GraphMessageBodySchema | None = None
    sender: GraphRecipientSchema | None = None
    from_: GraphRecipientSchema | None = Field(default=None, alias="from")
    to_recipients: list[GraphRecipientSchema] = Field(
        default_factory=list,
        alias="toRecipients",
    )
    cc_recipients: list[GraphRecipientSchema] = Field(
        default_factory=list,
        alias="ccRecipients",
    )
    bcc_recipients: list[GraphRecipientSchema] = Field(
        default_factory=list,
        alias="bccRecipients",
    )
    received_date_time: datetime | None = Field(default=None, alias="receivedDateTime")
    conversation_id: str | None = Field(default=None, alias="conversationId")
    is_read: bool | None = Field(default=None, alias="isRead")
    has_attachments: bool | None = Field(default=None, alias="hasAttachments")
    importance: str | None = None
    # Set by the poller; not a Graph JSON field.
    source_folder: str | None = None


class GraphMessageListSchema(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    value: list[GraphMessageSchema] = Field(default_factory=list)
    odata_next_link: str | None = Field(default=None, alias="@odata.nextLink")


class GraphSubscriptionSchema(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str
    resource: str
    change_type: str = Field(alias="changeType")
    notification_url: str = Field(alias="notificationUrl")
    lifecycle_notification_url: str | None = Field(
        default=None,
        alias="lifecycleNotificationUrl",
    )
    expiration_date_time: datetime = Field(alias="expirationDateTime")
    client_state: str | None = Field(default=None, alias="clientState")
    application_id: str | None = Field(default=None, alias="applicationId")
    creator_id: str | None = Field(default=None, alias="creatorId")


class SimulateIngestRequestSchema(BaseModel):
    mailbox: str = Field(max_length=320)
    message_id: str = Field(max_length=512)
    conversation_id: str = Field(max_length=512)
    sender: str = Field(max_length=320)
    subject: str = Field(max_length=998)
    body_text: str = Field(max_length=200_000)
    body_preview: str | None = Field(default=None, max_length=512)
    received_at: datetime
    to_recipients: list[str] = Field(default_factory=list, max_length=50)
    cc_recipients: list[str] = Field(default_factory=list, max_length=50)
    bcc_recipients: list[str] = Field(default_factory=list, max_length=50)
    has_attachments: bool = False


class IngestResultSchema(BaseModel):
    message_id: str
    status: Literal[
        "ingested",
        "duplicate",
        "skipped",
        "retry_triage",
        "in_flight",
        "outbound",
    ]
    thread_id: str | None = None
    conversation_id: str | None = None
    thread_context: ThreadContextSchema | None = None
    triage: TriageResultSchema | None = None
    draft: DraftSchema | None = None
    draft_status: DraftStatus | None = None
    prompt_version: str | None = None


class GraphCheckResponseSchema(BaseModel):
    auth: Literal["ok"]
    mailbox: str
    message_count: int
    sample_subjects: list[str] = Field(default_factory=list)
