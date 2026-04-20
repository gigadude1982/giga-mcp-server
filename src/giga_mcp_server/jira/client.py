from __future__ import annotations

import asyncio
from typing import Any

import structlog
from atlassian import Jira

from giga_mcp_server.config import Settings
from giga_mcp_server.models import IdeaResult, ParsedIdea
from giga_mcp_server.retry import async_retry

logger = structlog.get_logger()


# TODO: replace with Atlassian Rovo MCP server if cleaner
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

    @async_retry(max_attempts=3, base_delay=2.0)
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

    @async_retry(max_attempts=3, base_delay=1.0)
    async def search_issues(
        self, jql: str, max_results: int = 20
    ) -> list[dict[str, Any]]:
        """Search JIRA issues using JQL. Returns simplified issue dicts."""
        results = await asyncio.to_thread(
            self._jira.jql,
            jql,
            limit=max_results,
            fields="summary,status,priority,created",
        )
        issues = []
        for issue in results.get("issues", []):
            fields = issue["fields"]
            issues.append(
                {
                    "key": issue["key"],
                    "summary": fields.get("summary", ""),
                    "status": fields.get("status", {}).get("name", ""),
                    "priority": fields.get("priority", {}).get("name", ""),
                    "created": fields.get("created", ""),
                    "url": f"{self._settings.jira_url}/browse/{issue['key']}",
                }
            )
        return issues

    @async_retry(max_attempts=3, base_delay=1.0)
    async def add_comment(self, issue_key: str, body: str) -> bool:
        """Add a comment to a JIRA issue."""
        try:
            await asyncio.to_thread(self._jira.issue_add_comment, issue_key, body)
            return True
        except Exception:
            logger.exception("add_comment_error", issue_key=issue_key)
            return False

    async def transition_issue(self, issue_key: str, status: str) -> bool:
        """Transition a JIRA issue to a new status."""
        try:
            transitions = await asyncio.to_thread(
                self._jira.get_issue_transitions, issue_key
            )
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

    @async_retry(max_attempts=3, base_delay=1.0)
    async def get_issue(self, issue_key: str) -> dict[str, Any]:
        """Fetch full details for a single JIRA issue."""
        raw = await asyncio.to_thread(self._jira.issue, issue_key)
        fields = raw.get("fields", {})
        subtasks = [
            {"key": s["key"], "summary": s["fields"]["summary"]}
            for s in fields.get("subtasks", [])
        ]
        return {
            "key": raw["key"],
            "summary": fields.get("summary", ""),
            "description": fields.get("description") or "",
            "status": fields.get("status", {}).get("name", ""),
            "priority": fields.get("priority", {}).get("name", ""),
            "issue_type": fields.get("issuetype", {}).get("name", ""),
            "labels": fields.get("labels", []),
            "components": [c["name"] for c in fields.get("components", [])],
            "created": fields.get("created", ""),
            "updated": fields.get("updated", ""),
            "assignee": (fields.get("assignee") or {}).get("displayName", ""),
            "reporter": (fields.get("reporter") or {}).get("displayName", ""),
            "subtasks": subtasks,
            "parent": (fields.get("parent") or {}).get("key", ""),
            "url": f"{self._settings.jira_url}/browse/{raw['key']}",
        }

    @async_retry(max_attempts=3, base_delay=1.0)
    async def update_issue(self, issue_key: str, fields: dict[str, Any]) -> bool:
        """Update fields on an existing JIRA issue."""
        try:
            logger.info("updating_issue", issue_key=issue_key, fields=list(fields.keys()))
            await asyncio.to_thread(self._jira.update_issue_field, issue_key, fields)
            return True
        except Exception:
            logger.exception("update_issue_error", issue_key=issue_key)
            return False

    @async_retry(max_attempts=3, base_delay=2.0)
    async def create_subtask(
        self,
        parent_key: str,
        summary: str,
        description: str = "",
    ) -> IdeaResult:
        """Create a subtask linked to a parent issue."""
        fields: dict[str, Any] = {
            "project": {"key": self._settings.jira_project_key},
            "parent": {"key": parent_key},
            "summary": summary,
            "description": description,
            "issuetype": {"name": "Sub-task"},
        }
        logger.info("creating_subtask", parent=parent_key, summary=summary)
        result = await asyncio.to_thread(self._jira.create_issue, fields=fields)
        issue_key = result["key"]
        return IdeaResult(
            jira_key=issue_key,
            jira_url=f"{self._settings.jira_url}/browse/{issue_key}",
            summary=summary,
            status=self._settings.jira_intake_status,
        )

    @async_retry(max_attempts=3, base_delay=1.0)
    async def search_issues_full(
        self, jql: str, max_results: int = 20
    ) -> list[dict[str, Any]]:
        """Search JIRA issues using JQL, returning full details."""
        results = await asyncio.to_thread(
            self._jira.jql,
            jql,
            limit=max_results,
            fields="summary,status,priority,created,description,labels,issuetype,components,subtasks",
        )
        issues = []
        for issue in results.get("issues", []):
            f = issue["fields"]
            issues.append(
                {
                    "key": issue["key"],
                    "summary": f.get("summary", ""),
                    "description": f.get("description") or "",
                    "status": f.get("status", {}).get("name", ""),
                    "priority": f.get("priority", {}).get("name", ""),
                    "issue_type": f.get("issuetype", {}).get("name", ""),
                    "labels": f.get("labels", []),
                    "components": [c["name"] for c in f.get("components", [])],
                    "created": f.get("created", ""),
                    "subtasks": [
                        {"key": s["key"], "summary": s["fields"]["summary"]}
                        for s in f.get("subtasks", [])
                    ],
                    "url": f"{self._settings.jira_url}/browse/{issue['key']}",
                }
            )
        return issues
