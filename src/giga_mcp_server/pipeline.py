from __future__ import annotations

from difflib import SequenceMatcher
from typing import TYPE_CHECKING

import structlog

from giga_mcp_server.models import IdeaResult, WhatsAppMessage

if TYPE_CHECKING:
    from giga_mcp_server.config import Settings
    from giga_mcp_server.jira.client import JiraClient
    from giga_mcp_server.parser.base import MessageParser
    from giga_mcp_server.whatsapp.client import WhatsAppClient

logger = structlog.get_logger()

_DEDUP_THRESHOLD = 0.85  # Similarity ratio above which we consider a duplicate


class IdeaPipeline:
    """Orchestrates: WhatsApp message -> parse -> deduplicate -> JIRA issue -> confirmation."""

    def __init__(
        self,
        wa_client: WhatsAppClient,
        jira_client: JiraClient,
        parser: MessageParser,
        settings: Settings,
    ) -> None:
        self._wa_client = wa_client
        self._jira_client = jira_client
        self._parser = parser
        self._settings = settings
        self._recent_summaries: list[tuple[str, str]] = []  # (summary, jira_key)
        self._max_recent = 50

    async def process_message(self, msg: WhatsAppMessage) -> IdeaResult:
        """Full pipeline: parse a WhatsApp message, create a JIRA issue, send confirmation."""
        idea = self._parser.parse(msg.content, msg.sender, msg.timestamp)

        logger.info(
            "idea_parsed",
            summary=idea.summary,
            priority=idea.priority,
            issue_type=idea.issue_type,
            labels=idea.labels,
        )

        # Check for duplicates against recent issues and in-memory cache
        duplicate = self._find_duplicate_in_cache(idea.summary)
        if not duplicate:
            duplicate = await self._find_duplicate_in_jira(idea.summary)

        if duplicate:
            dup_key, dup_similarity = duplicate
            logger.info(
                "duplicate_detected",
                existing_key=dup_key,
                similarity=f"{dup_similarity:.0%}",
                new_summary=idea.summary,
            )
            comment = (
                f"Similar idea submitted via WhatsApp by {msg.sender}:\n\n"
                f"{msg.content}"
            )
            await self._jira_client.add_comment(dup_key, comment)

            confirmation = (
                f"💬 Added to existing *{dup_key}* (similar idea already exists)\n"
                f"{self._settings.jira_url}/browse/{dup_key}"
            )
            await self._wa_client.send_message(
                self._settings.whatsapp_group_jid, confirmation
            )
            return IdeaResult(
                jira_key=dup_key,
                jira_url=f"{self._settings.jira_url}/browse/{dup_key}",
                summary=idea.summary,
                status="duplicate",
            )

        result = await self._jira_client.create_story(idea)

        # Track in recent cache
        self._recent_summaries.append((idea.summary, result.jira_key))
        if len(self._recent_summaries) > self._max_recent:
            self._recent_summaries.pop(0)

        # Send confirmation back to the WhatsApp group
        confirmation = (
            f"✅ Created *{result.jira_key}*: {result.summary}\n"
            f"{result.jira_url}"
        )
        sent = await self._wa_client.send_message(
            self._settings.whatsapp_group_jid,
            confirmation,
        )
        if not sent:
            logger.warning("confirmation_not_sent", jira_key=result.jira_key)

        return result

    def _find_duplicate_in_cache(self, summary: str) -> tuple[str, float] | None:
        """Check the in-memory cache of recently created issues."""
        for existing_summary, jira_key in self._recent_summaries:
            ratio = SequenceMatcher(None, summary.lower(), existing_summary.lower()).ratio()
            if ratio >= _DEDUP_THRESHOLD:
                return jira_key, ratio
        return None

    async def _find_duplicate_in_jira(self, summary: str) -> tuple[str, float] | None:
        """Search recent JIRA issues for similar summaries."""
        s = self._settings
        jql = (
            f'project = "{s.jira_project_key}" '
            f"ORDER BY created DESC"
        )
        try:
            issues = await self._jira_client.search_issues(jql, max_results=30)
        except Exception:
            logger.exception("dedup_search_failed")
            return None

        for issue in issues:
            ratio = SequenceMatcher(
                None, summary.lower(), issue["summary"].lower()
            ).ratio()
            if ratio >= _DEDUP_THRESHOLD:
                return issue["key"], ratio
        return None
