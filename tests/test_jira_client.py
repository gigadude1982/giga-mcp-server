from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from giga_mcp_server.config import Settings
from giga_mcp_server.jira.client import JiraClient
from giga_mcp_server.models import ParsedIdea


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
    return settings


class TestJiraClient:
    @patch("giga_mcp_server.jira.client.Jira")
    async def test_create_story(self, MockJira: MagicMock, mock_settings: MagicMock) -> None:
        mock_jira_instance = MockJira.return_value
        mock_jira_instance.create_issue.return_value = {"key": "PROJ-99"}

        client = JiraClient(mock_settings)
        idea = ParsedIdea(
            summary="Build a dashboard",
            description="Full description here",
            priority="High",
            labels=["frontend", "analytics"],
            issue_type="Story",
        )
        result = await client.create_story(idea)

        assert result.jira_key == "PROJ-99"
        assert result.jira_url == "https://test.atlassian.net/browse/PROJ-99"
        assert result.summary == "Build a dashboard"

        call_args = mock_jira_instance.create_issue.call_args
        fields = call_args[1]["fields"]
        assert fields["project"]["key"] == "PROJ"
        assert fields["summary"] == "Build a dashboard"
        assert fields["issuetype"]["name"] == "Story"
        assert fields["priority"]["name"] == "High"
        assert fields["labels"] == ["frontend", "analytics"]

    @patch("giga_mcp_server.jira.client.Jira")
    async def test_create_story_without_labels(
        self, MockJira: MagicMock, mock_settings: MagicMock
    ) -> None:
        mock_jira_instance = MockJira.return_value
        mock_jira_instance.create_issue.return_value = {"key": "PROJ-100"}

        client = JiraClient(mock_settings)
        idea = ParsedIdea(summary="Simple idea", description="Details")
        result = await client.create_story(idea)

        assert result.jira_key == "PROJ-100"
        call_args = mock_jira_instance.create_issue.call_args
        fields = call_args[1]["fields"]
        assert "labels" not in fields

    @patch("giga_mcp_server.jira.client.Jira")
    async def test_search_issues(self, MockJira: MagicMock, mock_settings: MagicMock) -> None:
        mock_jira_instance = MockJira.return_value
        mock_jira_instance.jql.return_value = {
            "issues": [
                {
                    "key": "PROJ-1",
                    "fields": {
                        "summary": "Test issue",
                        "status": {"name": "To Do"},
                        "priority": {"name": "Medium"},
                        "created": "2026-04-16T12:00:00.000+0000",
                    },
                }
            ]
        }

        client = JiraClient(mock_settings)
        issues = await client.search_issues('project = "PROJ"')

        assert len(issues) == 1
        assert issues[0]["key"] == "PROJ-1"
        assert issues[0]["status"] == "To Do"

    @patch("giga_mcp_server.jira.client.Jira")
    async def test_transition_issue_success(
        self, MockJira: MagicMock, mock_settings: MagicMock
    ) -> None:
        mock_jira_instance = MockJira.return_value
        mock_jira_instance.get_issue_transitions.return_value = [
            {"id": "21", "name": "In Progress"},
            {"id": "31", "name": "Done"},
        ]

        client = JiraClient(mock_settings)
        result = await client.transition_issue("PROJ-1", "In Progress")

        assert result is True
        mock_jira_instance.set_issue_status.assert_called_once_with("PROJ-1", "In Progress")

    @patch("giga_mcp_server.jira.client.Jira")
    async def test_transition_issue_not_found(
        self, MockJira: MagicMock, mock_settings: MagicMock
    ) -> None:
        mock_jira_instance = MockJira.return_value
        mock_jira_instance.get_issue_transitions.return_value = [
            {"id": "21", "name": "In Progress"},
        ]

        client = JiraClient(mock_settings)
        result = await client.transition_issue("PROJ-1", "Nonexistent Status")

        assert result is False
