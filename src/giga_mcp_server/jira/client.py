from __future__ import annotations

import asyncio
from typing import Any

import structlog
from atlassian import Jira

from giga_mcp_server.config import Settings
from giga_mcp_server.models import IdeaResult, ParsedIdea

logger = structlog.get_logger()


class JiraClient:
    """Creates and manages JIRA issues using atlassian-python-api."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._jira = Jira(
            url=settings.jira_url,
            username=settings.jira_username,
            password=settings.jira_api_token,
            cloud=True,
        )

    async def create_story(self, idea: ParsedIdea) -> IdeaResult:
        """Create a JIRA issue from a parsed idea. Runs sync API in a thread."""
        return await asyncio.to_thread(self._create_story_sync, idea)

    def _create_story_sync(self, idea: ParsedIdea) -> IdeaResult:
        fields: dict[str, Any] = {
            "project": {"key": self._settings.jira_project_key},
            "summary": idea.summary,
            "description": idea.description,
            "issuetype": {"name": idea.issue_type or self._settings.jira_default_issue_type},
            "priority": {"name": idea.priority or self._settings.jira_default_priority},
        }

        if idea.labels:
            fields["labels"] = idea.labels

        if idea.components:
            fields["components"] = [{"name": c} for c in idea.components]

        logger.info(
            "creating_jira_issue",
            project=self._settings.jira_project_key,
            summary=idea.summary,
            issue_type=fields["issuetype"]["name"],
        )

        result = self._jira.create_issue(fields=fields)

        issue_key = result["key"]
        return IdeaResult(
            jira_key=issue_key,
            jira_url=f"{self._settings.jira_url}/browse/{issue_key}",
            summary=idea.summary,
            status=self._settings.jira_intake_status,
        )

    async def search_issues(self, jql: str, max_results: int = 20) -> list[dict[str, Any]]:
        """Search JIRA issues using JQL. Returns simplified issue dicts."""
        results = await asyncio.to_thread(
            self._jira.jql, jql, limit=max_results, fields="summary,status,priority,created"
        )
        issues = []
        for issue in results.get("issues", []):
            fields = issue["fields"]
            issues.append({
                "key": issue["key"],
                "summary": fields.get("summary", ""),
                "status": fields.get("status", {}).get("name", ""),
                "priority": fields.get("priority", {}).get("name", ""),
                "created": fields.get("created", ""),
                "url": f"{self._settings.jira_url}/browse/{issue['key']}",
            })
        return issues

    async def transition_issue(self, issue_key: str, status: str) -> bool:
        """Transition a JIRA issue to a new status."""
        try:
            transitions = await asyncio.to_thread(self._jira.get_issue_transitions, issue_key)
            target = next(
                (t for t in transitions if t["name"].lower() == status.lower()),
                None,
            )
            if not target:
                available = [t["name"] for t in transitions]
                logger.warning(
                    "transition_not_found",
                    issue_key=issue_key,
                    target=status,
                    available=available,
                )
                return False

            await asyncio.to_thread(
                self._jira.set_issue_status, issue_key, target["name"]
            )
            return True
        except Exception:
            logger.exception("transition_error", issue_key=issue_key)
            return False
