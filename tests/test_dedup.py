from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from giga_mcp_server.models import IdeaResult, WhatsAppMessage
from giga_mcp_server.parser.rule_based import RuleBasedParser
from giga_mcp_server.pipeline import IdeaPipeline


@pytest.fixture
def mock_settings() -> MagicMock:
    settings = MagicMock()
    settings.whatsapp_group_jid = "group@g.us"
    settings.jira_project_key = "PROJ"
    settings.jira_url = "https://test.atlassian.net"
    settings.jira_default_issue_type = "Story"
    settings.jira_default_priority = "Medium"
    settings.jira_intake_status = "To Do"
    return settings


@pytest.fixture
def mock_wa_client() -> AsyncMock:
    client = AsyncMock()
    client.send_message.return_value = True
    return client


@pytest.fixture
def mock_jira_client() -> AsyncMock:
    client = AsyncMock()
    client.create_story.return_value = IdeaResult(
        jira_key="PROJ-42",
        jira_url="https://test.atlassian.net/browse/PROJ-42",
        summary="Build a dashboard",
        status="To Do",
    )
    client.search_issues.return_value = []
    client.add_comment.return_value = True
    return client


def _make_msg(content: str, msg_id: str = "msg1") -> WhatsAppMessage:
    return WhatsAppMessage(
        id=msg_id,
        chat_jid="group@g.us",
        sender="+15551234567",
        content=content,
        timestamp=datetime(2026, 4, 17, 12, 0, 0, tzinfo=timezone.utc),
        is_from_me=False,
    )


class TestDeduplication:
    async def test_no_duplicate_creates_new_issue(
        self,
        mock_wa_client: AsyncMock,
        mock_jira_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        pipeline = IdeaPipeline(mock_wa_client, mock_jira_client, RuleBasedParser(), mock_settings)
        result = await pipeline.process_message(_make_msg("Build a user dashboard"))

        assert result.jira_key == "PROJ-42"
        mock_jira_client.create_story.assert_called_once()

    async def test_duplicate_in_cache_adds_comment(
        self,
        mock_wa_client: AsyncMock,
        mock_jira_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        pipeline = IdeaPipeline(mock_wa_client, mock_jira_client, RuleBasedParser(), mock_settings)

        # First message creates the issue
        await pipeline.process_message(_make_msg("Build a user dashboard"))

        # Second very similar message should be detected as duplicate
        mock_jira_client.create_story.reset_mock()
        result = await pipeline.process_message(
            _make_msg("Build a user dashboard", msg_id="msg2")
        )

        assert result.status == "duplicate"
        assert result.jira_key == "PROJ-42"
        mock_jira_client.create_story.assert_not_called()
        mock_jira_client.add_comment.assert_called_once()

    async def test_different_message_not_duplicate(
        self,
        mock_wa_client: AsyncMock,
        mock_jira_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        pipeline = IdeaPipeline(mock_wa_client, mock_jira_client, RuleBasedParser(), mock_settings)

        await pipeline.process_message(_make_msg("Build a user dashboard"))

        # Very different message should not be duplicate
        mock_jira_client.create_story.reset_mock()
        mock_jira_client.create_story.return_value = IdeaResult(
            jira_key="PROJ-43",
            jira_url="https://test.atlassian.net/browse/PROJ-43",
            summary="Fix login bug",
            status="To Do",
        )
        result = await pipeline.process_message(
            _make_msg("Fix the login bug on mobile", msg_id="msg2")
        )

        assert result.jira_key == "PROJ-43"
        mock_jira_client.create_story.assert_called_once()

    async def test_duplicate_found_in_jira_search(
        self,
        mock_wa_client: AsyncMock,
        mock_jira_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        # Simulate JIRA returning a similar existing issue
        mock_jira_client.search_issues.return_value = [
            {
                "key": "PROJ-10",
                "summary": "Build a user signup dashboard",
                "status": "To Do",
                "priority": "Medium",
                "created": "2026-04-16T12:00:00",
                "url": "https://test.atlassian.net/browse/PROJ-10",
            }
        ]

        pipeline = IdeaPipeline(mock_wa_client, mock_jira_client, RuleBasedParser(), mock_settings)
        result = await pipeline.process_message(
            _make_msg("Build a user signup dashboard")
        )

        assert result.status == "duplicate"
        assert result.jira_key == "PROJ-10"
        mock_jira_client.create_story.assert_not_called()
        mock_jira_client.add_comment.assert_called_once()

    async def test_jira_search_failure_does_not_block(
        self,
        mock_wa_client: AsyncMock,
        mock_jira_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        mock_jira_client.search_issues.side_effect = RuntimeError("JIRA down")

        pipeline = IdeaPipeline(mock_wa_client, mock_jira_client, RuleBasedParser(), mock_settings)
        result = await pipeline.process_message(_make_msg("Build something new"))

        # Should still create the issue despite search failure
        assert result.jira_key == "PROJ-42"
        mock_jira_client.create_story.assert_called_once()
