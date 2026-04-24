from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from giga_mcp_server.config import Board, Settings


def test_synthesis_from_legacy_scalars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Legacy-only env (no GIGA_BOARDS) synthesizes a single board."""
    monkeypatch.delenv("GIGA_BOARDS", raising=False)
    monkeypatch.setenv("GIGA_JIRA_PROJECT_KEY", "LEGACY")
    monkeypatch.setenv("GIGA_GITHUB_REPO", "org/legacy")
    monkeypatch.setenv("GIGA_GITHUB_BASE_BRANCH", "trunk")

    s = Settings(_env_file=None)

    assert len(s.boards) == 1
    assert s.boards[0].jira_project_key == "LEGACY"
    assert s.boards[0].github_repo == "org/legacy"
    assert s.boards[0].github_base_branch == "trunk"


def test_boards_from_env_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """GIGA_BOARDS env-JSON is parsed into a list of Board."""
    boards = [
        {"jira_project_key": "ABC", "github_repo": "org/abc"},
        {"jira_project_key": "XYZ", "github_repo": "org/xyz", "github_base_branch": "develop"},
    ]
    monkeypatch.setenv("GIGA_BOARDS", json.dumps(boards))
    monkeypatch.delenv("GIGA_JIRA_PROJECT_KEY", raising=False)
    monkeypatch.delenv("GIGA_GITHUB_REPO", raising=False)

    s = Settings(_env_file=None)

    assert len(s.boards) == 2
    assert s.boards[0].jira_project_key == "ABC"
    assert s.boards[0].github_base_branch == "main"  # default
    assert s.boards[1].github_base_branch == "develop"


def test_boards_takes_precedence_over_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    """When both GIGA_BOARDS and legacy fields are set, boards wins (no double-entry)."""
    monkeypatch.setenv("GIGA_BOARDS", '[{"jira_project_key":"ABC","github_repo":"org/abc"}]')
    monkeypatch.setenv("GIGA_JIRA_PROJECT_KEY", "IGNORED")
    monkeypatch.setenv("GIGA_GITHUB_REPO", "org/ignored")

    s = Settings(_env_file=None)

    assert len(s.boards) == 1
    assert s.boards[0].jira_project_key == "ABC"


def test_no_boards_no_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    """With neither boards nor legacy fields, boards stays empty and validate_required fails."""
    monkeypatch.delenv("GIGA_BOARDS", raising=False)
    monkeypatch.delenv("GIGA_JIRA_PROJECT_KEY", raising=False)
    monkeypatch.delenv("GIGA_GITHUB_REPO", raising=False)

    s = Settings(_env_file=None)

    assert s.boards == []
    with pytest.raises(ValueError, match="GIGA_BOARDS"):
        s.validate_required()


def test_get_board_lookup() -> None:
    s = Settings(
        _env_file=None,
        boards=[
            Board(jira_project_key="ABC", github_repo="org/abc"),
            Board(jira_project_key="XYZ", github_repo="org/xyz"),
        ],
    )
    assert s.get_board("ABC").github_repo == "org/abc"
    assert s.get_board("XYZ").github_repo == "org/xyz"
    with pytest.raises(KeyError, match="NOPE"):
        s.get_board("NOPE")


def test_board_for_issue() -> None:
    s = Settings(
        _env_file=None,
        boards=[
            Board(jira_project_key="ABC", github_repo="org/abc"),
            Board(jira_project_key="XYZ", github_repo="org/xyz"),
        ],
    )
    assert s.board_for_issue("ABC-42").jira_project_key == "ABC"
    assert s.board_for_issue("XYZ-1").jira_project_key == "XYZ"
    with pytest.raises(KeyError):
        s.board_for_issue("UNKNOWN-1")


def test_default_board() -> None:
    s = Settings(
        _env_file=None,
        boards=[
            Board(jira_project_key="ABC", github_repo="org/abc"),
            Board(jira_project_key="XYZ", github_repo="org/xyz"),
        ],
    )
    assert s.default_board().jira_project_key == "ABC"


def test_invalid_board_missing_required() -> None:
    with pytest.raises(ValidationError):
        Board(jira_project_key="ABC")  # type: ignore[call-arg]
