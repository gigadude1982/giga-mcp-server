from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from giga_mcp_server.config import Board, Settings


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

    board = Board(jira_project_key="PROJ", github_repo="org/proj", github_base_branch="main")
    settings.boards = [board]
    settings.get_board = lambda key: (
        board if key == "PROJ" else (_ for _ in ()).throw(KeyError(key))
    )
    settings.board_for_issue = lambda issue_key: board
    settings.default_board = lambda: board
    return settings


@pytest.fixture
def multi_board_settings() -> MagicMock:
    settings = MagicMock(spec=Settings)
    settings.jira_url = "https://test.atlassian.net"
    settings.jira_username = "user@test.com"
    settings.jira_api_token = "test-token"
    settings.jira_default_issue_type = "Story"
    settings.jira_default_priority = "Medium"
    settings.jira_intake_status = "To Do"
    settings.jira_processed_label = "ai-processed"
    settings.anthropic_api_key = "test-key"
    settings.anthropic_model = "claude-haiku-4-5-20251001"

    abc = Board(jira_project_key="ABC", github_repo="org/abc", github_base_branch="main")
    xyz = Board(jira_project_key="XYZ", github_repo="org/xyz", github_base_branch="develop")
    settings.boards = [abc, xyz]
    boards_by_key = {"ABC": abc, "XYZ": xyz}

    def _get_board(key: str) -> Board:
        if key in boards_by_key:
            return boards_by_key[key]
        raise KeyError(f"Unknown board project_key {key!r}; known: {list(boards_by_key)}")

    settings.get_board = _get_board
    settings.board_for_issue = lambda issue_key: _get_board(issue_key.split("-", 1)[0])
    settings.default_board = lambda: abc
    return settings
