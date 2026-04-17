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
    settings.whatsapp_group_jid = "120363001234567890@g.us"
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
    return client


@pytest.fixture
def pipeline(
    mock_wa_client: AsyncMock,
    mock_jira_client: AsyncMock,
    mock_settings: MagicMock,
) -> IdeaPipeline:
    return IdeaPipeline(
        wa_client=mock_wa_client,
        jira_client=mock_jira_client,
        parser=RuleBasedParser(),
        settings=mock_settings,
    )


class TestIdeaPipeline:
    async def test_process_message_creates_jira_issue(
        self,
        pipeline: IdeaPipeline,
        mock_jira_client: AsyncMock,
    ) -> None:
        msg = WhatsAppMessage(
            id="msg1",
            chat_jid="120363001234567890@g.us",
            sender="+15551234567",
            content="Build a dashboard for user signups #frontend",
            timestamp=datetime(2026, 4, 16, 12, 0, 0, tzinfo=timezone.utc),
            is_from_me=False,
        )
        result = await pipeline.process_message(msg)

        assert result.jira_key == "PROJ-42"
        mock_jira_client.create_story.assert_called_once()

        idea = mock_jira_client.create_story.call_args[0][0]
        assert "dashboard" in idea.summary.lower()
        assert "frontend" in idea.labels
        assert idea.issue_type == "Story"

    async def test_process_message_sends_confirmation(
        self,
        pipeline: IdeaPipeline,
        mock_wa_client: AsyncMock,
    ) -> None:
        msg = WhatsAppMessage(
            id="msg1",
            chat_jid="120363001234567890@g.us",
            sender="+15551234567",
            content="Build a dashboard",
            timestamp=datetime(2026, 4, 16, 12, 0, 0, tzinfo=timezone.utc),
            is_from_me=False,
        )
        await pipeline.process_message(msg)

        mock_wa_client.send_message.assert_called_once()
        call_args = mock_wa_client.send_message.call_args
        assert "PROJ-42" in call_args[0][1]  # Confirmation text contains issue key

    async def test_process_message_returns_result_even_if_confirmation_fails(
        self,
        pipeline: IdeaPipeline,
        mock_wa_client: AsyncMock,
    ) -> None:
        mock_wa_client.send_message.return_value = False
        msg = WhatsAppMessage(
            id="msg1",
            chat_jid="120363001234567890@g.us",
            sender="+15551234567",
            content="Some idea",
            timestamp=datetime(2026, 4, 16, 12, 0, 0, tzinfo=timezone.utc),
            is_from_me=False,
        )
        result = await pipeline.process_message(msg)
        assert result.jira_key == "PROJ-42"  # Still succeeds

    async def test_process_urgent_bug_message(
        self,
        pipeline: IdeaPipeline,
        mock_jira_client: AsyncMock,
    ) -> None:
        msg = WhatsAppMessage(
            id="msg2",
            chat_jid="120363001234567890@g.us",
            sender="+15559876543",
            content="urgent: fix login bug on mobile app",
            timestamp=datetime(2026, 4, 16, 12, 0, 0, tzinfo=timezone.utc),
            is_from_me=False,
        )
        await pipeline.process_message(msg)

        idea = mock_jira_client.create_story.call_args[0][0]
        assert idea.priority == "High"
        assert idea.issue_type == "Bug"
