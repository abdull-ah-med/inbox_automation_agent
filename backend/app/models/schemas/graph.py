from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class GraphNotificationResourceDataSchema(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    odata_type: str | None = Field(default=None, alias="@odata.type")
    odata_id: str | None = Field(default=None, alias="@odata.id")
    odata_etag: str | None = Field(default=None, alias="@odata.etag")
    id: str | None = None


class GraphNotificationItemSchema(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str | None = None
    subscription_id: str = Field(alias="subscriptionId")
    client_state: str | None = Field(default=None, alias="clientState")
    change_type: Literal["created", "updated", "deleted"] = Field(alias="changeType")
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
    received_date_time: datetime | None = Field(default=None, alias="receivedDateTime")
    conversation_id: str | None = Field(default=None, alias="conversationId")
    is_read: bool | None = Field(default=None, alias="isRead")
    has_attachments: bool | None = Field(default=None, alias="hasAttachments")
    importance: str | None = None


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
    expiration_date_time: datetime = Field(alias="expirationDateTime")
    client_state: str | None = Field(default=None, alias="clientState")
    application_id: str | None = Field(default=None, alias="applicationId")
    creator_id: str | None = Field(default=None, alias="creatorId")


class SimulateIngestRequestSchema(BaseModel):
    mailbox: str
    message_id: str
    conversation_id: str
    sender: str
    subject: str
    body_text: str
    body_preview: str | None = None
    received_at: datetime


class IngestResultSchema(BaseModel):
    message_id: str
    status: Literal["ingested", "duplicate", "skipped"]
    thread_id: str | None = None
    conversation_id: str | None = None


class GraphCheckResponseSchema(BaseModel):
    auth: Literal["ok"]
    mailbox: str
    message_count: int
    sample_subjects: list[str] = Field(default_factory=list)
