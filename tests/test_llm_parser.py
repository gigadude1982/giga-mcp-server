from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from giga_mcp_server.parser.llm_parser import LLMParser


@pytest.fixture
def mock_anthropic():
    with patch("giga_mcp_server.parser.llm_parser.anthropic") as mock_mod:
        mock_client = MagicMock()
        mock_mod.Anthropic.return_value = mock_client
        yield mock_client


def _make_response(data: dict) -> MagicMock:
    """Create a mock Anthropic API response."""
    response = MagicMock()
    block = MagicMock()
    block.text = json.dumps(data)
    response.content = [block]
    return response


class TestLLMParser:
    def test_requires_api_key(self) -> None:
        with pytest.raises(ValueError, match="GIGA_ANTHROPIC_API_KEY"):
            LLMParser(api_key=None)

    def test_parse_basic_message(self, mock_anthropic: MagicMock) -> None:
        mock_anthropic.messages.create.return_value = _make_response({
            "summary": "Build user signup dashboard",
            "description": "Create a dashboard to track user signups",
            "priority": "Medium",
            "labels": ["frontend", "analytics"],
            "issue_type": "Story",
        })

        parser = LLMParser(api_key="test-key")
        idea = parser.parse("Build a dashboard for user signups #frontend", "alice")

        assert idea.summary == "Build user signup dashboard"
        assert idea.priority == "Medium"
        assert "frontend" in idea.labels
        assert idea.issue_type == "Story"
        assert idea.sender == "alice"
        assert idea.raw_message == "Build a dashboard for user signups #frontend"

    def test_parse_urgent_bug(self, mock_anthropic: MagicMock) -> None:
        mock_anthropic.messages.create.return_value = _make_response({
            "summary": "Fix login crash on Android",
            "description": "Users report crash on login screen on Android devices",
            "priority": "High",
            "labels": ["android", "auth"],
            "issue_type": "Bug",
        })

        parser = LLMParser(api_key="test-key")
        idea = parser.parse("urgent: login is crashing on android!!", "bob")

        assert idea.priority == "High"
        assert idea.issue_type == "Bug"

    def test_falls_back_on_api_error(self, mock_anthropic: MagicMock) -> None:
        mock_anthropic.messages.create.side_effect = RuntimeError("API down")

        parser = LLMParser(api_key="test-key")
        idea = parser.parse("Build a dashboard #frontend", "alice")

        # Should fall back to rule-based parser
        assert "dashboard" in idea.summary.lower()
        assert idea.issue_type == "Story"
        assert "frontend" in idea.labels

    def test_falls_back_on_invalid_json(self, mock_anthropic: MagicMock) -> None:
        response = MagicMock()
        block = MagicMock()
        block.text = "This is not JSON"
        response.content = [block]
        mock_anthropic.messages.create.return_value = response

        parser = LLMParser(api_key="test-key")
        idea = parser.parse("Some idea", "alice")

        # Should fall back to rule-based parser
        assert idea.summary is not None

    def test_empty_message_uses_fallback(self, mock_anthropic: MagicMock) -> None:
        parser = LLMParser(api_key="test-key")
        idea = parser.parse("", "alice")

        assert idea.summary == "Empty message"
        mock_anthropic.messages.create.assert_not_called()

    def test_truncates_long_summary(self, mock_anthropic: MagicMock) -> None:
        mock_anthropic.messages.create.return_value = _make_response({
            "summary": "A" * 200,
            "description": "Details",
            "priority": "Medium",
            "labels": [],
            "issue_type": "Story",
        })

        parser = LLMParser(api_key="test-key")
        idea = parser.parse("Long message", "alice")

        assert len(idea.summary) <= 120
