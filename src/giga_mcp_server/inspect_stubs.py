"""Mock clients for MCP Inspector / dry-run mode.

Returns plausible fake data so all tools can be exercised without
real JIRA or Anthropic credentials.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any

from giga_mcp_server.config import Settings
from giga_mcp_server.models import (
    EnrichmentResult,
    IdeaResult,
    ParsedIdea,
    SubtaskSuggestion,
    TicketAnalysis,
)

_FAKE_TICKETS = [
    ("Build a dashboard for tracking user signups", "Story", "Medium"),
    ("Fix login crash on Android", "Bug", "High"),
    ("Add dark mode to the settings page", "Story", "Low"),
    ("Update API docs for v2 endpoints", "Task", "Medium"),
]

_ISSUE_COUNTER = 100


class MockJiraClient:
    """Returns fake JIRA responses for inspector testing."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._counter = _ISSUE_COUNTER

    async def create_ticket(self, idea: ParsedIdea) -> IdeaResult:
        self._counter += 1
        key = f"DEMO-{self._counter}"
        return IdeaResult(
            jira_key=key,
            jira_url=f"https://demo.atlassian.net/browse/{key}",
            summary=idea.summary,
            status="To Do",
        )

    async def search_issues(self, jql: str, max_results: int = 20) -> list[dict[str, Any]]:
        return [
            {
                "key": f"DEMO-{i}",
                "summary": summary,
                "status": "To Do",
                "priority": priority,
                "created": (datetime.now(timezone.utc) - timedelta(hours=i)).isoformat(),
                "url": f"https://demo.atlassian.net/browse/DEMO-{i}",
            }
            for i, (summary, _, priority) in enumerate(_FAKE_TICKETS, start=1)
        ]

    async def search_issues_full(
        self, jql: str, max_results: int = 20
    ) -> list[dict[str, Any]]:
        return [
            {
                "key": f"DEMO-{i}",
                "summary": summary,
                "description": f"Details for: {summary}",
                "status": "To Do",
                "priority": priority,
                "issue_type": issue_type,
                "labels": [],
                "components": [],
                "created": (datetime.now(timezone.utc) - timedelta(hours=i)).isoformat(),
                "subtasks": [],
                "url": f"https://demo.atlassian.net/browse/DEMO-{i}",
            }
            for i, (summary, issue_type, priority) in enumerate(_FAKE_TICKETS, start=1)
        ]

    async def get_issue(self, issue_key: str) -> dict[str, Any]:
        idx = random.randint(0, len(_FAKE_TICKETS) - 1)
        summary, issue_type, priority = _FAKE_TICKETS[idx]
        return {
            "key": issue_key,
            "summary": summary,
            "description": f"Details for: {summary}",
            "status": "To Do",
            "priority": priority,
            "issue_type": issue_type,
            "labels": [],
            "components": [],
            "created": datetime.now(timezone.utc).isoformat(),
            "updated": datetime.now(timezone.utc).isoformat(),
            "assignee": "",
            "reporter": "Mock User",
            "subtasks": [],
            "parent": "",
            "url": f"https://demo.atlassian.net/browse/{issue_key}",
        }

    async def update_issue(self, issue_key: str, fields: dict[str, Any]) -> bool:
        return True

    async def create_subtask(
        self, parent_key: str, summary: str, description: str = ""
    ) -> IdeaResult:
        self._counter += 1
        key = f"DEMO-{self._counter}"
        return IdeaResult(
            jira_key=key,
            jira_url=f"https://demo.atlassian.net/browse/{key}",
            summary=summary,
            status="To Do",
        )

    async def add_comment(self, issue_key: str, body: str) -> bool:
        return True

    async def transition_issue(self, issue_key: str, status: str) -> bool:
        return True


class MockTicketEnricher:
    """Returns fake enrichment results for inspector testing."""

    def __init__(self, jira_client: MockJiraClient, settings: Settings) -> None:
        self._jira = jira_client
        self._settings = settings

    async def create_ticket(
        self, description: str, auto_enrich: bool = True
    ) -> IdeaResult:
        key = f"DEMO-{random.randint(200, 299)}"
        return IdeaResult(
            jira_key=key,
            jira_url=f"https://demo.atlassian.net/browse/{key}",
            summary=description[:80],
            status="To Do",
        )

    async def analyze_ticket(self, issue_key: str) -> TicketAnalysis:
        return TicketAnalysis(
            issue_key=issue_key,
            suggested_priority="High",
            suggested_type="Story",
            suggested_labels=["frontend", "dashboard"],
            acceptance_criteria=[
                "Given a logged-in user, When they navigate to /dashboard, Then they see signup metrics",
                "Given the dashboard, When data is loading, Then a skeleton loader is shown",
            ],
            enriched_description="Build a dashboard for tracking user signups.\n\n"
            "This feature should provide real-time visibility into signup metrics.",
            should_split=True,
            subtask_suggestions=[
                SubtaskSuggestion(
                    summary="Design dashboard wireframes",
                    description="Create wireframes for the signup tracking dashboard.",
                ),
                SubtaskSuggestion(
                    summary="Implement API endpoint for signup data",
                    description="Create GET /api/signups endpoint with date range filtering.",
                ),
            ],
            confidence=0.87,
            reasoning="The ticket describes a new feature with multiple components. "
            "Splitting into design and implementation subtasks for better tracking.",
        )

    async def enrich_ticket(
        self, issue_key: str, analysis: TicketAnalysis | None = None
    ) -> EnrichmentResult:
        return EnrichmentResult(
            issue_key=issue_key,
            fields_updated=["description", "priority", "labels"],
            subtasks_created=["DEMO-101", "DEMO-102"],
            comment_added=True,
        )

    async def find_duplicates(self, issue_key: str) -> list[tuple[str, float]]:
        return [("DEMO-3", 0.92)]

    async def process_backlog(self, limit: int = 10) -> list[EnrichmentResult]:
        return [
            EnrichmentResult(
                issue_key=f"DEMO-{i}",
                fields_updated=["description", "priority", "labels"],
                comment_added=True,
            )
            for i in range(1, min(limit, 4) + 1)
        ]
