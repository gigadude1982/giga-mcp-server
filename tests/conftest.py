from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from giga_mcp_server.config import Settings


@pytest.fixture
def mock_settings() -> MagicMock:
    settings = MagicMock(spec=Settings)
    settings.jira_url = "https://test.atlassian.net"
    settings.jira_username = "user@test.com"
    settings.jira_api_token = "test-token"
    settings.jira_project_key = "PROJ"
    settings.jira_default_issue_type = "Story"
    settings.jira_default_priority = "Medium"
    settings.jira_intake_status = "To Do"
    settings.jira_processed_label = "ai-processed"
    settings.anthropic_api_key = "test-key"
    settings.anthropic_model = "claude-haiku-4-5-20251001"
    return settings
