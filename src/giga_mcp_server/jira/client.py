from __future__ import annotations

import asyncio
from typing import Any

import structlog
from atlassian import Jira

from giga_mcp_server.config import Settings
from giga_mcp_server.models import IdeaResult, ParsedIdea
from giga_mcp_server.retry import async_retry


def _adf_to_text(node: Any) -> str:
    """Extract plain text from an ADF node (JIRA Cloud comment/description format)."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        if text := node.get("text"):
            return text
        parts = [_adf_to_text(child) for child in node.get("content", [])]
        sep = "\n" if node.get("type") in ("paragraph", "heading", "bulletList") else " "
        return sep.join(p for p in parts if p)
    return ""

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
    async def get_project_issue_types(self) -> list[str]:
        """Return the issue type names available for the configured project."""
        try:
            result = await asyncio.to_thread(
                self._jira.get,
                f"/rest/api/3/project/{self._settings.jira_project_key}",
            )
            types = result.get("issueTypes", [])
            return [t["name"] for t in types if not t.get("subtask", False)]
        except Exception:
            return []

    def _resolve_issue_type(self, requested: str, valid_types: list[str]) -> str:
        """Map a requested issue type to the nearest valid one for the project.

        Falls back to the configured default, then to the first available type.
        """
        if not valid_types:
            return requested or self._settings.jira_default_issue_type

        # Exact match (case-insensitive)
        for t in valid_types:
            if t.lower() == (requested or "").lower():
                return t

        # Semantic fallbacks: map common AI-generated names to JIRA equivalents
        fallback_map = {
            "story": ["story", "user story", "task", "feature"],
            "bug": ["bug", "defect", "issue", "problem"],
            "task": ["task", "chore", "maintenance", "subtask"],
            "epic": ["epic", "initiative"],
            "feature": ["feature", "story", "task"],
        }
        requested_lower = (requested or "").lower()
        for candidate in fallback_map.get(requested_lower, []):
            for t in valid_types:
                if t.lower() == candidate:
                    return t

        # Fall back to configured default, then first available
        default = self._settings.jira_default_issue_type
        for t in valid_types:
            if t.lower() == default.lower():
                return t
        return valid_types[0]

    async def create_ticket(self, idea: ParsedIdea) -> IdeaResult:
        """Create a JIRA issue from a parsed idea. Runs sync API in a thread."""
        valid_types = await self.get_project_issue_types()
        resolved_type = self._resolve_issue_type(idea.issue_type, valid_types)
        return await asyncio.to_thread(self._create_ticket_sync, idea, resolved_type)

    def _create_ticket_sync(self, idea: ParsedIdea, resolved_issue_type: str) -> IdeaResult:
        fields: dict[str, Any] = {
            "project": {"key": self._settings.jira_project_key},
            "summary": idea.summary,
            "description": idea.description,
            "issuetype": {"name": resolved_issue_type},
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
    async def get_comments(self, issue_key: str, max_comments: int = 10) -> list[str]:
        """Return the most recent non-pipeline comment bodies for a ticket."""
        try:
            raw = await asyncio.to_thread(
                self._jira.issue_get_comments, issue_key
            )
            comments = raw.get("comments", []) if isinstance(raw, dict) else []
            # Filter out pipeline-generated comments (start with known prefixes)
            pipeline_prefixes = (
                "🤖", "Autonomous pipeline", "CI failed", "Digester", "Plan:",
                "Implementation plan", "Pipeline", "plan for"
            )
            user_comments = [
                _adf_to_text(c.get("body", "")) if isinstance(c.get("body"), dict)
                else str(c.get("body", ""))
                for c in comments
                if not any(
                    (_adf_to_text(c.get("body", "")) if isinstance(c.get("body"), dict)
                     else str(c.get("body", ""))).startswith(p)
                    for p in pipeline_prefixes
                )
            ]
            return [c for c in user_comments if c.strip()][-max_comments:]
        except Exception:
            logger.warning("get_comments_error", issue_key=issue_key)
            return []

    async def add_comment(self, issue_key: str, body: str) -> bool:
        """Add a comment to a JIRA issue."""
        try:
            await asyncio.to_thread(self._jira.issue_add_comment, issue_key, body)
            return True
        except Exception:
            logger.exception("add_comment_error", issue_key=issue_key)
            return False

    async def _ensure_status_exists(self, status_name: str) -> bool:
        """Create a JIRA status if it doesn't already exist.

        Uses the Jira Cloud statuses API. The status is created in the
        IN_PROGRESS category so it appears in the active work swim lane.
        Note: creating the status does not automatically add it to a project
        workflow — a JIRA admin must add it to the relevant workflow transition
        for it to become available as a transition target.
        """
        try:
            existing = await asyncio.to_thread(
                self._jira.get, "/rest/api/3/statuses"
            )
            if any(
                s.get("name", "").lower() == status_name.lower()
                for s in (existing if isinstance(existing, list) else [])
            ):
                return True  # already exists

            await asyncio.to_thread(
                self._jira.post,
                "/rest/api/3/statuses",
                json={"statuses": [{"name": status_name, "statusCategory": "IN_PROGRESS"}]},
            )
            logger.info("jira_status_created", status=status_name)
            return True
        except Exception:
            logger.warning("jira_status_create_failed", status=status_name)
            return False

    async def transition_issue(self, issue_key: str, status: str) -> bool:
        """Transition a JIRA issue to a new status.

        If the transition is not available, attempts to create the status via
        the JIRA API as a safety net, then logs guidance for the admin to wire
        it into the project workflow.
        """
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
                # Attempt to create the status so it exists in JIRA — a project
                # admin still needs to add it to the workflow transition for it
                # to become available as a transition target.
                await self._ensure_status_exists(status)
                logger.warning(
                    "transition_skipped_add_to_workflow",
                    status=status,
                    hint=f"Add '{status}' to the project workflow in JIRA admin to enable this transition",
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
