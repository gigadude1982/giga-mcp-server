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

    # Auth (Cognito) — leave blank to disable auth
    cognito_user_pool_id: str = ""
    cognito_region: str = "us-east-1"
    cognito_client_id: str = ""
    public_url: str = ""  # e.g. https://mcp.gigacorp.co — used for OAuth resource metadata

    # GitHub — required for autonomous pipeline
    github_token: str = Field(default="", description="GitHub PAT (repo + workflow scopes)")
    github_repo: str = Field(default="", description="Target repo in owner/repo format")
    github_base_branch: str = "main"
    # TODO: replace with a dedicated GitHub bot account (e.g. giga-bot) once IaC and Auth
    # are complete. A bot account will show a proper bot badge in GitHub UI. For now, commits
    # are attributed to the PAT owner but authored as the name/email below.
    pipeline_commit_author_name: str = "giga-pipeline[bot]"
    pipeline_commit_author_email: str = "giga-pipeline[bot]@users.noreply.github.com"

    # Pipeline behaviour
    pipeline_human_gate: bool = True   # pause after Planner for human approval
    pipeline_max_retries: int = 3

    # Server
    server_name: str = "giga-mcp-server"
    transport: str = "stdio"  # "stdio" or "streamable-http"
    host: str = "0.0.0.0"
    port: int = 8000
    inspect: bool = False
    log_file: str | None = None

    @property
    def auth_enabled(self) -> bool:
        return bool(self.cognito_user_pool_id)

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
