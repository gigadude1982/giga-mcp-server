from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="GIGA_", case_sensitive=False)

    # JIRA
    jira_url: str = Field(default="", description="Atlassian instance URL")
    jira_username: str = Field(default="", description="Atlassian account email")
    jira_api_token: str = Field(default="", description="Atlassian API token")
    jira_project_key: str = Field(default="", description="JIRA project key for story creation")
    jira_default_issue_type: str = "Story"
    jira_default_priority: str = "Medium"
    jira_intake_status: str = "To Do"
    jira_processed_label: str = "ai-processed"

    # AI / Anthropic
    anthropic_api_key: str = Field(default="", description="Anthropic API key for Claude")
    anthropic_model: str = "claude-haiku-4-5-20251001"

    # Server
    transport: str = "stdio"  # "stdio" or "streamable-http"
    host: str = "0.0.0.0"
    port: int = 8000
    inspect: bool = False
    log_file: str | None = None

    def validate_required(self) -> None:
        """Validate that required fields are set for production."""
        missing = []
        if not self.jira_url:
            missing.append("GIGA_JIRA_URL")
        if not self.jira_username:
            missing.append("GIGA_JIRA_USERNAME")
        if not self.jira_api_token:
            missing.append("GIGA_JIRA_API_TOKEN")
        if not self.jira_project_key:
            missing.append("GIGA_JIRA_PROJECT_KEY")
        if not self.anthropic_api_key:
            missing.append("GIGA_ANTHROPIC_API_KEY")
        if missing:
            raise ValueError(f"Missing required env vars: {', '.join(missing)}")
