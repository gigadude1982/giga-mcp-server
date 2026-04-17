from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="GIGA_", case_sensitive=False)

    # WhatsApp
    whatsapp_bridge_url: str = "http://localhost:8080"
    whatsapp_db_path: str = "./whatsapp-bridge/store/messages.db"
    whatsapp_group_jid: str = Field(
        default="",
        description="Target WhatsApp group JID (ends with @g.us)",
    )
    whatsapp_poll_interval_seconds: int = 10

    # JIRA
    jira_url: str = Field(default="", description="Atlassian instance URL")
    jira_username: str = Field(default="", description="Atlassian account email")
    jira_api_token: str = Field(default="", description="Atlassian API token")
    jira_project_key: str = Field(default="", description="JIRA project key for story creation")
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
    inspect: bool = False  # Run with mock clients for MCP Inspector
    log_file: str | None = None  # Optional path to write logs (e.g. ./giga.log)

    def validate_required(self) -> None:
        """Validate that required fields are set. Call this in production lifespan."""
        missing = []
        if not self.whatsapp_group_jid:
            missing.append("GIGA_WHATSAPP_GROUP_JID")
        if not self.jira_url:
            missing.append("GIGA_JIRA_URL")
        if not self.jira_username:
            missing.append("GIGA_JIRA_USERNAME")
        if not self.jira_api_token:
            missing.append("GIGA_JIRA_API_TOKEN")
        if not self.jira_project_key:
            missing.append("GIGA_JIRA_PROJECT_KEY")
        if missing:
            raise ValueError(f"Missing required env vars: {', '.join(missing)}")
