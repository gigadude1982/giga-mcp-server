from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

import structlog
from mcp.server.fastmcp import FastMCP

from giga_mcp_server.config import Settings
from giga_mcp_server.jira.client import JiraClient
from giga_mcp_server.models import WhatsAppMessage
from giga_mcp_server.parser.base import MessageParser
from giga_mcp_server.parser.rule_based import RuleBasedParser
from giga_mcp_server.pipeline import IdeaPipeline
from giga_mcp_server.whatsapp.client import WhatsAppClient
from giga_mcp_server.whatsapp.poller import Poller

logger = structlog.get_logger()


@dataclass
class AppContext:
    pipeline: IdeaPipeline
    wa_client: WhatsAppClient
    jira_client: JiraClient
    poller: Poller
    settings: Settings


def _get_parser(settings: Settings) -> MessageParser:
    if settings.parser_type == "llm":
        from giga_mcp_server.parser.llm_parser import LLMParser

        return LLMParser(api_key=settings.anthropic_api_key)
    return RuleBasedParser()


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    settings = Settings()
    wa_client = WhatsAppClient(
        db_path=settings.whatsapp_db_path,
        bridge_url=settings.whatsapp_bridge_url,
    )
    jira_client = JiraClient(settings)
    parser = _get_parser(settings)
    pipeline = IdeaPipeline(wa_client, jira_client, parser, settings)
    poller = Poller(
        wa_client=wa_client,
        pipeline=pipeline,
        group_jid=settings.whatsapp_group_jid,
        poll_interval=settings.whatsapp_poll_interval_seconds,
    )

    poll_task = asyncio.create_task(poller.run())
    logger.info("server_started", transport=settings.transport)

    try:
        yield AppContext(
            pipeline=pipeline,
            wa_client=wa_client,
            jira_client=jira_client,
            poller=poller,
            settings=settings,
        )
    finally:
        poll_task.cancel()
        try:
            await poll_task
        except asyncio.CancelledError:
            pass
        logger.info("server_stopped")


mcp = FastMCP("giga-mcp-server", lifespan=lifespan)


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def process_message(message: str, sender: str = "unknown") -> str:
    """Manually process a message through the pipeline: parse it and create a JIRA story.

    Args:
        message: The idea or thought to create a JIRA story from.
        sender: Who submitted this idea.
    """
    ctx: AppContext = mcp.get_context().request_context
    from datetime import datetime, timezone

    msg = WhatsAppMessage(
        id="manual",
        chat_jid="manual",
        sender=sender,
        content=message,
        timestamp=datetime.now(timezone.utc),
        is_from_me=False,
    )
    result = await ctx.pipeline.process_message(msg)
    return f"Created {result.jira_key}: {result.summary}\n{result.jira_url}"


@mcp.tool()
async def list_pending_ideas(limit: int = 20) -> str:
    """List recent ideas in the JIRA intake/backlog column awaiting review.

    Args:
        limit: Maximum number of ideas to return.
    """
    ctx: AppContext = mcp.get_context().request_context
    s = ctx.settings
    jql = (
        f'project = "{s.jira_project_key}" '
        f'AND status = "{s.jira_intake_status}" '
        f"ORDER BY created DESC"
    )
    issues = await ctx.jira_client.search_issues(jql, max_results=limit)
    if not issues:
        return "No pending ideas found."

    lines = []
    for i in issues:
        lines.append(f"- **{i['key']}** [{i['priority']}] {i['summary']}  \n  {i['url']}")
    return "\n".join(lines)


@mcp.tool()
async def get_group_messages(since_minutes: int = 60) -> str:
    """Fetch recent messages from the configured WhatsApp group.

    Args:
        since_minutes: How many minutes back to look.
    """
    from datetime import datetime, timedelta, timezone

    ctx: AppContext = mcp.get_context().request_context
    since = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
    messages = await ctx.wa_client.get_new_messages(
        since=since,
        chat_jid=ctx.settings.whatsapp_group_jid,
    )
    if not messages:
        return f"No messages in the last {since_minutes} minutes."

    lines = []
    for m in messages:
        direction = "→" if m.is_from_me else "←"
        lines.append(f"{direction} [{m.timestamp:%H:%M}] {m.sender}: {m.content}")
    return "\n".join(lines)


@mcp.tool()
async def update_idea_status(jira_key: str, status: str) -> str:
    """Transition a JIRA idea/story to a new status (e.g., 'In Progress', 'Done').

    Args:
        jira_key: The JIRA issue key, e.g. PROJ-123.
        status: The target status name.
    """
    ctx: AppContext = mcp.get_context().request_context
    success = await ctx.jira_client.transition_issue(jira_key, status)
    if success:
        return f"Moved {jira_key} to '{status}'."
    return f"Failed to transition {jira_key} to '{status}'. Check available transitions."


@mcp.tool()
async def get_pipeline_status() -> str:
    """Get the health status of the WhatsApp polling pipeline."""
    ctx: AppContext = mcp.get_context().request_context
    stats = ctx.poller.stats
    lines = [
        f"Poll interval: {stats['poll_interval_seconds']}s",
        f"Group JID: {stats['group_jid']}",
        f"Last poll: {stats['last_poll_time'] or 'never'}",
        f"Watermark: {stats['last_seen_timestamp']}",
        f"Messages processed: {stats['processed_count']}",
        f"Errors: {stats['error_count']}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    settings = Settings()
    if settings.transport == "streamable-http":
        mcp.run(transport="streamable-http", host=settings.host, port=settings.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
