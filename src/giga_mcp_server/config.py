from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="GIGA_", case_sensitive=False)

    # WhatsApp
    whatsapp_bridge_url: str = "http://localhost:8080"
    whatsapp_db_path: str = "./whatsapp-bridge/store/messages.db"
    whatsapp_group_jid: str = Field(description="Target WhatsApp group JID (ends with @g.us)")
    whatsapp_poll_interval_seconds: int = 10

    # JIRA
    jira_url: str = Field(description="Atlassian instance URL, e.g. https://company.atlassian.net")
    jira_username: str = Field(description="Atlassian account email")
    jira_api_token: str = Field(description="Atlassian API token")
    jira_project_key: str = Field(description="JIRA project key for story creation")
    jira_default_issue_type: str = "Story"
    jira_default_priority: str = "Medium"
    jira_intake_status: str = "To Do"

    # Parser
    parser_type: str = "rule_based"  # "rule_based" or "llm"
    anthropic_api_key: str | None = None

    # Server
    transport: str = "stdio"  # "stdio" or "streamable-http"
    host: str = "0.0.0.0"
    port: int = 8000
