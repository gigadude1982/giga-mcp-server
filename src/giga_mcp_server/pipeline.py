from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from giga_mcp_server.models import IdeaResult, WhatsAppMessage

if TYPE_CHECKING:
    from giga_mcp_server.config import Settings
    from giga_mcp_server.jira.client import JiraClient
    from giga_mcp_server.parser.base import MessageParser
    from giga_mcp_server.whatsapp.client import WhatsAppClient

logger = structlog.get_logger()


class IdeaPipeline:
    """Orchestrates: WhatsApp message -> parse -> JIRA issue -> confirmation."""

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

        result = await self._jira_client.create_story(idea)

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
