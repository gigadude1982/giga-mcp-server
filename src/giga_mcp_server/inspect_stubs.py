"""Mock clients for MCP Inspector / dry-run mode.

Returns plausible fake data so all tools can be exercised without
real WhatsApp or JIRA credentials.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any

from giga_mcp_server.models import IdeaResult, ParsedIdea, WhatsAppMessage


_FAKE_MESSAGES = [
    ("Build a dashboard for tracking user signups #frontend", "+15551234567"),
    ("urgent: fix login crash on Android", "+15559876543"),
    ("Add dark mode to the settings page #design #frontend", "+15551112222"),
    ("task: update API docs for v2 endpoints", "+15553334444"),
]

_ISSUE_COUNTER = 100


class MockWhatsAppClient:
    """Returns fake WhatsApp messages for inspector testing."""

    async def get_new_messages(
        self, since: datetime, chat_jid: str | None = None
    ) -> list[WhatsAppMessage]:
        now = datetime.now(timezone.utc)
        messages = []
        for i, (content, sender) in enumerate(_FAKE_MESSAGES):
            messages.append(
                WhatsAppMessage(
                    id=f"mock-{i}",
                    chat_jid=chat_jid or "mock-group@g.us",
                    sender=sender,
                    content=content,
                    timestamp=now - timedelta(minutes=30 - i * 5),
                    is_from_me=False,
                )
            )
        return messages

    async def send_message(self, jid: str, text: str) -> bool:
        return True


class MockJiraClient:
    """Returns fake JIRA responses for inspector testing."""

    def __init__(self) -> None:
        self._counter = _ISSUE_COUNTER

    async def create_story(self, idea: ParsedIdea) -> IdeaResult:
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
                "summary": msg,
                "status": "To Do",
                "priority": random.choice(["High", "Medium", "Low"]),
                "created": (
                    datetime.now(timezone.utc) - timedelta(hours=i)
                ).isoformat(),
                "url": f"https://demo.atlassian.net/browse/DEMO-{i}",
            }
            for i, (msg, _) in enumerate(_FAKE_MESSAGES, start=1)
        ]

    async def transition_issue(self, issue_key: str, status: str) -> bool:
        return True


class MockPoller:
    """No-op poller for inspect mode."""

    @property
    def stats(self) -> dict:
        return {
            "last_poll_time": None,
            "last_seen_timestamp": datetime.now(timezone.utc).isoformat(),
            "processed_count": 0,
            "error_count": 0,
            "group_jid": "mock-group@g.us",
            "poll_interval_seconds": 10,
        }

    async def run(self) -> None:
        pass  # No-op — never polls
