from __future__ import annotations

from unittest.mock import MagicMock, patch

from giga_mcp_server.jira.client import JiraClient
from giga_mcp_server.models import ParsedIdea


class TestJiraClient:
    @patch("giga_mcp_server.jira.client.Jira")
    async def test_create_ticket(self, MockJira: MagicMock, mock_settings: MagicMock) -> None:
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
        result = await client.create_ticket(idea)

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
    async def test_create_ticket_without_labels(
        self, MockJira: MagicMock, mock_settings: MagicMock
    ) -> None:
        mock_jira_instance = MockJira.return_value
        mock_jira_instance.create_issue.return_value = {"key": "PROJ-100"}

        client = JiraClient(mock_settings)
        idea = ParsedIdea(summary="Simple idea", description="Details")
        result = await client.create_ticket(idea)

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

    @patch("giga_mcp_server.jira.client.Jira")
    async def test_get_issue(self, MockJira: MagicMock, mock_settings: MagicMock) -> None:
        mock_jira_instance = MockJira.return_value
        mock_jira_instance.issue.return_value = {
            "key": "PROJ-42",
            "fields": {
                "summary": "Fix login bug",
                "description": "Users cannot log in on mobile",
                "status": {"name": "To Do"},
                "priority": {"name": "High"},
                "issuetype": {"name": "Bug"},
                "labels": ["auth"],
                "components": [{"name": "backend"}],
                "created": "2026-04-16T12:00:00.000+0000",
                "updated": "2026-04-17T09:00:00.000+0000",
                "assignee": {"displayName": "Alice"},
                "reporter": {"displayName": "Bob"},
                "subtasks": [],
                "parent": None,
            },
        }

        client = JiraClient(mock_settings)
        issue = await client.get_issue("PROJ-42")

        assert issue["key"] == "PROJ-42"
        assert issue["summary"] == "Fix login bug"
        assert issue["description"] == "Users cannot log in on mobile"
        assert issue["priority"] == "High"
        assert issue["issue_type"] == "Bug"
        assert issue["labels"] == ["auth"]
        assert issue["components"] == ["backend"]
        assert issue["assignee"] == "Alice"
        assert issue["reporter"] == "Bob"

    @patch("giga_mcp_server.jira.client.Jira")
    async def test_update_issue(self, MockJira: MagicMock, mock_settings: MagicMock) -> None:
        mock_jira_instance = MockJira.return_value

        client = JiraClient(mock_settings)
        result = await client.update_issue(
            "PROJ-42", {"priority": {"name": "High"}, "labels": ["urgent"]}
        )

        assert result is True
        mock_jira_instance.update_issue_field.assert_called_once_with(
            "PROJ-42", {"priority": {"name": "High"}, "labels": ["urgent"]}
        )

    @patch("giga_mcp_server.jira.client.Jira")
    async def test_update_issue_failure(
        self, MockJira: MagicMock, mock_settings: MagicMock
    ) -> None:
        mock_jira_instance = MockJira.return_value
        mock_jira_instance.update_issue_field.side_effect = Exception("API error")

        client = JiraClient(mock_settings)
        result = await client.update_issue("PROJ-42", {"priority": {"name": "High"}})

        assert result is False

    @patch("giga_mcp_server.jira.client.Jira")
    async def test_create_subtask(self, MockJira: MagicMock, mock_settings: MagicMock) -> None:
        mock_jira_instance = MockJira.return_value
        mock_jira_instance.create_issue.return_value = {"key": "PROJ-43"}

        client = JiraClient(mock_settings)
        result = await client.create_subtask(
            parent_key="PROJ-42",
            summary="Implement login fix",
            description="Fix the OAuth flow on mobile",
        )

        assert result.jira_key == "PROJ-43"
        assert result.summary == "Implement login fix"

        call_args = mock_jira_instance.create_issue.call_args
        fields = call_args[1]["fields"]
        assert fields["parent"]["key"] == "PROJ-42"
        assert fields["issuetype"]["name"] == "Sub-task"
        assert fields["summary"] == "Implement login fix"

    @patch("giga_mcp_server.jira.client.Jira")
    async def test_search_issues_full(
        self, MockJira: MagicMock, mock_settings: MagicMock
    ) -> None:
        mock_jira_instance = MockJira.return_value
        mock_jira_instance.jql.return_value = {
            "issues": [
                {
                    "key": "PROJ-1",
                    "fields": {
                        "summary": "Test issue",
                        "description": "Detailed description",
                        "status": {"name": "To Do"},
                        "priority": {"name": "Medium"},
                        "issuetype": {"name": "Story"},
                        "labels": ["frontend"],
                        "components": [{"name": "ui"}],
                        "created": "2026-04-16T12:00:00.000+0000",
                        "subtasks": [],
                    },
                }
            ]
        }

        client = JiraClient(mock_settings)
        issues = await client.search_issues_full('project = "PROJ"')

        assert len(issues) == 1
        assert issues[0]["key"] == "PROJ-1"
        assert issues[0]["description"] == "Detailed description"
        assert issues[0]["issue_type"] == "Story"
        assert issues[0]["labels"] == ["frontend"]
        assert issues[0]["components"] == ["ui"]
