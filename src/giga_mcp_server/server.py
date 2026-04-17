from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

import structlog
from mcp.server.fastmcp import Context, FastMCP

from giga_mcp_server.config import Settings
from giga_mcp_server.jira.client import JiraClient
from giga_mcp_server.models import WhatsAppMessage
from giga_mcp_server.parser.base import MessageParser
from giga_mcp_server.parser.rule_based import RuleBasedParser
from giga_mcp_server.pipeline import IdeaPipeline
from giga_mcp_server.whatsapp.client import WhatsAppClient
from giga_mcp_server.whatsapp.poller import Poller

def _configure_logging(log_file: str | None) -> None:
    """Set up structlog to write to stderr and optionally a file."""
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file:
        handlers.append(logging.FileHandler(log_file))

    logging.basicConfig(
        format="%(message)s",
        handlers=handlers,
        level=logging.INFO,
        force=True,
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


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
async def _inspect_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """Lifespan with mock clients for MCP Inspector / dry-run mode."""
    from giga_mcp_server.inspect_stubs import MockJiraClient, MockPoller, MockWhatsAppClient

    settings = Settings()
    wa_client = MockWhatsAppClient()
    jira_client = MockJiraClient()
    parser = _get_parser(settings)
    pipeline = IdeaPipeline(wa_client, jira_client, parser, settings)  # type: ignore[arg-type]
    poller = MockPoller()

    logger.info("server_started", transport=settings.transport, mode="inspect")
    yield AppContext(
        pipeline=pipeline,
        wa_client=wa_client,  # type: ignore[arg-type]
        jira_client=jira_client,  # type: ignore[arg-type]
        poller=poller,  # type: ignore[arg-type]
        settings=settings,
    )
    logger.info("server_stopped")


@asynccontextmanager
async def _production_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    settings = Settings()
    settings.validate_required()

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


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    settings = Settings()
    _configure_logging(settings.log_file)
    ctx_manager = _inspect_lifespan if settings.inspect else _production_lifespan
    async with ctx_manager(server) as ctx:
        yield ctx


mcp = FastMCP("giga-mcp-server", lifespan=lifespan)


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


def _app(ctx: Context) -> AppContext:
    """Extract our AppContext from the MCP request context."""
    return ctx.request_context.lifespan_context


@mcp.tool()
async def process_message(message: str, sender: str = "unknown", ctx: Context = None) -> str:
    """Manually process a message through the pipeline: parse it and create a JIRA story.

    Args:
        message: The idea or thought to create a JIRA story from.
        sender: Who submitted this idea.
    """
    from datetime import datetime, timezone

    app = _app(ctx)
    msg = WhatsAppMessage(
        id="manual",
        chat_jid="manual",
        sender=sender,
        content=message,
        timestamp=datetime.now(timezone.utc),
        is_from_me=False,
    )
    result = await app.pipeline.process_message(msg)
    return f"Created {result.jira_key}: {result.summary}\n{result.jira_url}"


@mcp.tool()
async def list_pending_ideas(limit: int = 20, ctx: Context = None) -> str:
    """List recent ideas in the JIRA intake/backlog column awaiting review.

    Args:
        limit: Maximum number of ideas to return.
    """
    app = _app(ctx)
    s = app.settings
    jql = (
        f'project = "{s.jira_project_key}" '
        f'AND status = "{s.jira_intake_status}" '
        f"ORDER BY created DESC"
    )
    issues = await app.jira_client.search_issues(jql, max_results=limit)
    if not issues:
        return "No pending ideas found."

    lines = []
    for i in issues:
        lines.append(f"- **{i['key']}** [{i['priority']}] {i['summary']}  \n  {i['url']}")
    return "\n".join(lines)


@mcp.tool()
async def get_group_messages(since_minutes: int = 60, ctx: Context = None) -> str:
    """Fetch recent messages from the configured WhatsApp group.

    Args:
        since_minutes: How many minutes back to look.
    """
    from datetime import datetime, timedelta, timezone

    app = _app(ctx)
    since = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
    messages = await app.wa_client.get_new_messages(
        since=since,
        chat_jid=app.settings.whatsapp_group_jid,
    )
    if not messages:
        return f"No messages in the last {since_minutes} minutes."

    lines = []
    for m in messages:
        direction = "→" if m.is_from_me else "←"
        lines.append(f"{direction} [{m.timestamp:%H:%M}] {m.sender}: {m.content}")
    return "\n".join(lines)


@mcp.tool()
async def update_idea_status(jira_key: str, status: str, ctx: Context = None) -> str:
    """Transition a JIRA idea/story to a new status (e.g., 'In Progress', 'Done').

    Args:
        jira_key: The JIRA issue key, e.g. PROJ-123.
        status: The target status name.
    """
    app = _app(ctx)
    success = await app.jira_client.transition_issue(jira_key, status)
    if success:
        return f"Moved {jira_key} to '{status}'."
    return f"Failed to transition {jira_key} to '{status}'. Check available transitions."


@mcp.tool()
async def get_pipeline_status(ctx: Context = None) -> str:
    """Get the health status of the WhatsApp polling pipeline."""
    app = _app(ctx)
    stats = app.poller.stats
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
    import os as _os

    if "--inspect" in sys.argv:
        _os.environ.setdefault("GIGA_INSPECT", "true")

    settings = Settings()
    _configure_logging(settings.log_file)

    if settings.inspect:
        logger.info("inspect_mode", hint="Running with mock clients — no real services needed")
    if settings.log_file:
        logger.info("logging_to_file", path=settings.log_file)

    if settings.transport == "streamable-http":
        mcp.run(transport="streamable-http", host=settings.host, port=settings.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
