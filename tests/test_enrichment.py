from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from giga_mcp_server.enrichment import TicketEnricher
from giga_mcp_server.models import IdeaResult, SubtaskSuggestion, TicketAnalysis


@pytest.fixture
def mock_jira(mock_settings: MagicMock) -> AsyncMock:
    jira = AsyncMock()
    jira.get_issue.return_value = {
        "key": "PROJ-42",
        "summary": "Add user signup dashboard",
        "description": "We need a dashboard",
        "status": "To Do",
        "priority": "Medium",
        "issue_type": "Story",
        "labels": [],
        "components": [],
        "created": "2026-04-16T12:00:00.000+0000",
        "updated": "2026-04-16T12:00:00.000+0000",
        "assignee": "",
        "reporter": "Test User",
        "subtasks": [],
        "parent": "",
        "url": "https://test.atlassian.net/browse/PROJ-42",
    }
    jira.search_issues.return_value = [
        {
            "key": "PROJ-1",
            "summary": "Existing ticket about login",
            "status": "To Do",
            "priority": "Medium",
            "created": "2026-04-15T12:00:00.000+0000",
            "url": "https://test.atlassian.net/browse/PROJ-1",
        },
    ]
    jira.update_issue.return_value = True
    jira.add_comment.return_value = True
    jira.create_subtask.return_value = IdeaResult(
        jira_key="PROJ-43",
        jira_url="https://test.atlassian.net/browse/PROJ-43",
        summary="Subtask",
        status="To Do",
    )
    return jira


@pytest.fixture
def claude_response() -> dict:
    return {
        "suggested_priority": "High",
        "suggested_type": "Story",
        "suggested_labels": ["frontend", "dashboard"],
        "acceptance_criteria": [
            "Given a user, When they visit /dashboard, Then signup metrics are shown"
        ],
        "enriched_description": "We need a dashboard.\n\nThis should show signup metrics.",
        "should_split": True,
        "subtask_suggestions": [
            {"summary": "Design wireframes", "description": "Create mockups"},
            {"summary": "Build API endpoint", "description": "GET /api/signups"},
        ],
        "duplicate_of": None,
        "confidence": 0.88,
        "reasoning": "Feature request with multiple components.",
    }


class TestTicketEnricher:
    @patch("giga_mcp_server.enrichment.anthropic.AsyncAnthropic")
    async def test_analyze_ticket(
        self,
        MockAnthropic: MagicMock,
        mock_jira: AsyncMock,
        mock_settings: MagicMock,
        claude_response: dict,
    ) -> None:
        mock_client = AsyncMock()
        MockAnthropic.return_value = mock_client
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text=json.dumps(claude_response))]
        mock_client.messages.create.return_value = mock_msg

        enricher = TicketEnricher(mock_jira, mock_settings)
        analysis = await enricher.analyze_ticket("PROJ-42")

        assert analysis.issue_key == "PROJ-42"
        assert analysis.suggested_priority == "High"
        assert analysis.suggested_type == "Story"
        assert analysis.suggested_labels == ["frontend", "dashboard"]
        assert len(analysis.acceptance_criteria) == 1
        assert analysis.should_split is True
        assert len(analysis.subtask_suggestions) == 2
        assert analysis.subtask_suggestions[0].summary == "Design wireframes"
        assert analysis.confidence == 0.88

    @patch("giga_mcp_server.enrichment.anthropic.AsyncAnthropic")
    async def test_analyze_ticket_strips_markdown_fences(
        self,
        MockAnthropic: MagicMock,
        mock_jira: AsyncMock,
        mock_settings: MagicMock,
        claude_response: dict,
    ) -> None:
        mock_client = AsyncMock()
        MockAnthropic.return_value = mock_client
        fenced = f"```json\n{json.dumps(claude_response)}\n```"
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text=fenced)]
        mock_client.messages.create.return_value = mock_msg

        enricher = TicketEnricher(mock_jira, mock_settings)
        analysis = await enricher.analyze_ticket("PROJ-42")

        assert analysis.issue_key == "PROJ-42"
        assert analysis.suggested_priority == "High"

    @patch("giga_mcp_server.enrichment.anthropic.AsyncAnthropic")
    async def test_enrich_ticket_updates_fields(
        self,
        MockAnthropic: MagicMock,
        mock_jira: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        MockAnthropic.return_value = AsyncMock()

        analysis = TicketAnalysis(
            issue_key="PROJ-42",
            suggested_priority="High",
            suggested_type="Story",
            suggested_labels=["frontend"],
            acceptance_criteria=["AC1"],
            enriched_description="Enriched desc",
            should_split=False,
        )

        enricher = TicketEnricher(mock_jira, mock_settings)
        result = await enricher.enrich_ticket("PROJ-42", analysis)

        assert "description" in result.fields_updated
        assert "priority" in result.fields_updated
        assert "labels" in result.fields_updated
        assert result.comment_added is True
        mock_jira.update_issue.assert_called_once()

    @patch("giga_mcp_server.enrichment.anthropic.AsyncAnthropic")
    async def test_enrich_ticket_creates_subtasks(
        self,
        MockAnthropic: MagicMock,
        mock_jira: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        MockAnthropic.return_value = AsyncMock()

        analysis = TicketAnalysis(
            issue_key="PROJ-42",
            suggested_priority="Medium",
            suggested_type="Story",
            suggested_labels=[],
            acceptance_criteria=[],
            enriched_description="Desc",
            should_split=True,
            subtask_suggestions=[
                SubtaskSuggestion(summary="Sub 1", description="Desc 1"),
                SubtaskSuggestion(summary="Sub 2", description="Desc 2"),
            ],
        )

        enricher = TicketEnricher(mock_jira, mock_settings)
        result = await enricher.enrich_ticket("PROJ-42", analysis)

        assert len(result.subtasks_created) == 2
        assert mock_jira.create_subtask.call_count == 2

    @patch("giga_mcp_server.enrichment.anthropic.AsyncAnthropic")
    async def test_enrich_ticket_flags_duplicate(
        self,
        MockAnthropic: MagicMock,
        mock_jira: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        MockAnthropic.return_value = AsyncMock()

        analysis = TicketAnalysis(
            issue_key="PROJ-42",
            suggested_priority="Medium",
            suggested_type="Story",
            suggested_labels=[],
            acceptance_criteria=[],
            enriched_description="",
            should_split=False,
            duplicate_of="PROJ-10",
            confidence=0.95,
            reasoning="Very similar to PROJ-10",
        )

        enricher = TicketEnricher(mock_jira, mock_settings)
        result = await enricher.enrich_ticket("PROJ-42", analysis)

        assert result.duplicate_of == "PROJ-10"
        assert result.comment_added is True
        # Should NOT update fields when it's a duplicate
        assert not result.fields_updated
        mock_jira.update_issue.assert_not_called()

    @patch("giga_mcp_server.enrichment.anthropic.AsyncAnthropic")
    async def test_enrich_ticket_adds_processed_label(
        self,
        MockAnthropic: MagicMock,
        mock_jira: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        MockAnthropic.return_value = AsyncMock()

        analysis = TicketAnalysis(
            issue_key="PROJ-42",
            suggested_priority="Medium",
            suggested_type="Story",
            suggested_labels=["api"],
            acceptance_criteria=[],
            enriched_description="Desc",
            should_split=False,
        )

        enricher = TicketEnricher(mock_jira, mock_settings)
        await enricher.enrich_ticket("PROJ-42", analysis)

        call_args = mock_jira.update_issue.call_args
        fields = call_args[0][1]
        assert "ai-processed" in fields["labels"]

    @patch("giga_mcp_server.enrichment.anthropic.AsyncAnthropic")
    async def test_find_duplicates(
        self,
        MockAnthropic: MagicMock,
        mock_jira: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        MockAnthropic.return_value = AsyncMock()

        # Set up a near-duplicate
        mock_jira.get_issue.return_value = {
            "key": "PROJ-42",
            "summary": "Add user signup dashboard",
            "description": "",
            "status": "To Do",
            "priority": "Medium",
            "issue_type": "Story",
            "labels": [],
            "components": [],
            "created": "2026-04-16T12:00:00.000+0000",
            "updated": "2026-04-16T12:00:00.000+0000",
            "assignee": "",
            "reporter": "",
            "subtasks": [],
            "parent": "",
            "url": "https://test.atlassian.net/browse/PROJ-42",
        }
        mock_jira.search_issues.return_value = [
            {
                "key": "PROJ-10",
                "summary": "Add user signup dashboard page",
                "status": "To Do",
                "priority": "Medium",
                "created": "2026-04-14T12:00:00.000+0000",
                "url": "https://test.atlassian.net/browse/PROJ-10",
            },
            {
                "key": "PROJ-5",
                "summary": "Completely unrelated ticket",
                "status": "Done",
                "priority": "Low",
                "created": "2026-04-10T12:00:00.000+0000",
                "url": "https://test.atlassian.net/browse/PROJ-5",
            },
        ]

        enricher = TicketEnricher(mock_jira, mock_settings)
        matches = await enricher.find_duplicates("PROJ-42")

        assert len(matches) == 1
        assert matches[0][0] == "PROJ-10"
        assert matches[0][1] >= 0.85

    @patch("giga_mcp_server.enrichment.anthropic.AsyncAnthropic")
    async def test_process_backlog(
        self,
        MockAnthropic: MagicMock,
        mock_jira: AsyncMock,
        mock_settings: MagicMock,
        claude_response: dict,
    ) -> None:
        mock_client = AsyncMock()
        MockAnthropic.return_value = mock_client
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text=json.dumps(claude_response))]
        mock_client.messages.create.return_value = mock_msg

        mock_jira.search_issues.return_value = [
            {
                "key": "PROJ-42",
                "summary": "Dashboard",
                "status": "To Do",
                "priority": "Medium",
                "created": "2026-04-16",
                "url": "https://test.atlassian.net/browse/PROJ-42",
            },
        ]

        enricher = TicketEnricher(mock_jira, mock_settings)
        results = await enricher.process_backlog(limit=5)

        assert len(results) == 1
        assert results[0].issue_key == "PROJ-42"
