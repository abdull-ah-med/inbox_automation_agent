from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    anthropic_api_key: str = ""
    graph_client_id: str = ""
    graph_client_secret: str = ""
    graph_tenant_id: str = ""
    graph_webhook_client_state: str = ""
    graph_notification_url: str = ""
    target_mailboxes: str = ""
    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    slack_review_channel_id: str = ""
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage"
    redis_url: str = "redis://localhost:6379/0"
    environment: Literal["local", "staging", "production"] = "local"
    staleness_threshold_hours: int = Field(default=24, ge=1)

    classification_model: str = "claude-haiku-4-5"
    draft_model: str = "claude-sonnet-4-6"

    @property
    def mailbox_list(self) -> list[str]:
        if not self.target_mailboxes.strip():
            return []
        return [m.strip() for m in self.target_mailboxes.split(",") if m.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
