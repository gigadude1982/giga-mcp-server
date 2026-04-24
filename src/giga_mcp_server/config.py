from __future__ import annotations

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Board(BaseModel):
    """A JIRA project routed to a specific GitHub repo."""

    jira_project_key: str
    github_repo: str
    github_base_branch: str = "main"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="GIGA_", case_sensitive=False)

    # JIRA
    jira_url: str = Field(default="", description="Atlassian instance URL")
    jira_username: str = Field(default="", description="Atlassian account email")
    jira_api_token: str = Field(default="", description="Atlassian API token")
    jira_project_key: str = Field(
        default="", description="JIRA project key for story creation (legacy single-board)"
    )
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
    github_repo: str = Field(
        default="", description="Target repo in owner/repo format (legacy single-board)"
    )
    github_base_branch: str = "main"
    # TODO: replace with a dedicated GitHub bot account (e.g. giga-bot) once IaC and Auth
    # are complete. A bot account will show a proper bot badge in GitHub UI. For now, commits
    # are attributed to the PAT owner but authored as the name/email below.
    pipeline_commit_author_name: str = "giga-pipeline[bot]"
    pipeline_commit_author_email: str = "giga-pipeline[bot]@users.noreply.github.com"

    # Multi-board — set GIGA_BOARDS='[{"jira_project_key":"ABC","github_repo":"org/abc"}, ...]'
    # If unset, a single board is synthesized from the legacy scalar fields above.
    boards: list[Board] = Field(default_factory=list)

    # Pipeline behaviour
    pipeline_human_gate: bool = True  # pause after Planner for human approval
    pipeline_max_retries: int = 3

    # Server
    transport: str = "stdio"  # "stdio" or "streamable-http"
    host: str = "0.0.0.0"
    port: int = 8000
    inspect: bool = False
    log_file: str | None = None

    @model_validator(mode="after")
    def _synthesize_boards(self) -> Settings:
        """If no boards configured, synthesize one from legacy scalars."""
        if not self.boards and self.jira_project_key and self.github_repo:
            self.boards = [
                Board(
                    jira_project_key=self.jira_project_key,
                    github_repo=self.github_repo,
                    github_base_branch=self.github_base_branch,
                )
            ]
        return self

    @property
    def auth_enabled(self) -> bool:
        return bool(self.cognito_user_pool_id)

    def get_board(self, project_key: str) -> Board:
        """Return the board with the given JIRA project key."""
        for board in self.boards:
            if board.jira_project_key == project_key:
                return board
        known = [b.jira_project_key for b in self.boards]
        raise KeyError(f"Unknown board project_key {project_key!r}; known: {known}")

    def board_for_issue(self, issue_key: str) -> Board:
        """Resolve the board for a JIRA issue key like 'ABC-123'."""
        prefix = issue_key.split("-", 1)[0]
        return self.get_board(prefix)

    def default_board(self) -> Board:
        """Return the first configured board. Raises if none configured."""
        if not self.boards:
            raise ValueError("No boards configured")
        return self.boards[0]

    def validate_required(self) -> None:
        """Validate that required fields are set for production."""
        missing = []
        if not self.jira_url:
            missing.append("GIGA_JIRA_URL")
        if not self.jira_username:
            missing.append("GIGA_JIRA_USERNAME")
        if not self.jira_api_token:
            missing.append("GIGA_JIRA_API_TOKEN")
        if not self.boards:
            missing.append("GIGA_BOARDS (or legacy GIGA_JIRA_PROJECT_KEY + GIGA_GITHUB_REPO)")
        if not self.anthropic_api_key:
            missing.append("GIGA_ANTHROPIC_API_KEY")
        if missing:
            raise ValueError(f"Missing required env vars: {', '.join(missing)}")
