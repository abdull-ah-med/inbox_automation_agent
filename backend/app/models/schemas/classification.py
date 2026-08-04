from typing import Literal

from pydantic import BaseModel, model_validator

from app.models.schemas.routing import RoutingCategory


class ClassificationSchema(BaseModel):
    category: Literal["CLIENT", "VENDOR", "SALES", "INTERMEDIARY"]
    intent: Literal["REPLY_NEEDED", "FORWARD", "DOCUMENT_REQUEST", "FYI", "ESCALATE"]
    urgency: Literal["CRITICAL", "HIGH", "NORMAL", "LOW"]
    urgency_reason: str
    rule_hint: str | None = None


class EntitiesSchema(BaseModel):
    client_name: str | None = None
    vendor_name: str | None = None
    county: str | None = None
    state_scope: Literal["county", "statewide"] | None = None
    drug_screen_status: str | None = None
    invoice_number: str | None = None
    contract_ref: str | None = None
    sku: str | None = None
    pricing_mentioned: bool = False
    order_ref: str | None = None


class ClassificationResultSchema(BaseModel):
    classification: ClassificationSchema
    entities: EntitiesSchema


class TriageResultSchema(BaseModel):
    """Haiku triage output — boolean flags + routing category (no numeric score)."""

    is_spam: bool
    spam_reason: str | None = None
    has_action_items: bool
    action_items_summary: str | None = None
    needs_context: bool
    context_reason: str | None = None
    routing_category: RoutingCategory

    @model_validator(mode="before")
    @classmethod
    def _default_routing_category(cls, data: object) -> object:
        """Allow legacy fixtures/callers missing routing_category during rollout."""
        if isinstance(data, dict) and data.get("routing_category") is None:
            data = {**data, "routing_category": "general"}
        return data
