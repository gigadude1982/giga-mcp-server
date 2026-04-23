from __future__ import annotations

import importlib.metadata
import logging
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

import structlog
from mcp.server.fastmcp import Context, FastMCP

from giga_mcp_server.config import Settings
from giga_mcp_server.enrichment import TicketEnricher
from giga_mcp_server.jira.client import JiraClient
from giga_mcp_server.pipeline.orchestrator import PipelineOrchestrator, PipelineState


def _configure_logging(log_file: str | None) -> None:
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
    jira_client: JiraClient
    enricher: TicketEnricher
    settings: Settings
    pipeline: PipelineOrchestrator
    pipeline_runs: dict[str, PipelineState]


@asynccontextmanager
async def _inspect_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """Lifespan for MCP Inspector: mock clients for dry-run testing."""
    from giga_mcp_server.inspect_stubs import MockJiraClient, MockTicketEnricher

    settings = Settings()
    jira_client = MockJiraClient(settings)
    enricher = MockTicketEnricher(jira_client, settings)
    pipeline = PipelineOrchestrator(settings, jira_client)

    logger.info("server_started", version=_VERSION, transport=settings.transport, mode="inspect")
    yield AppContext(
        jira_client=jira_client,
        enricher=enricher,
        settings=settings,
        pipeline=pipeline,
        pipeline_runs={},
    )
    logger.info("server_stopped")


@asynccontextmanager
async def _production_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    settings = Settings()
    settings.validate_required()

    jira_client = JiraClient(settings)
    enricher = TicketEnricher(jira_client, settings)
    pipeline = PipelineOrchestrator(settings, jira_client)

    logger.info("server_started", version=_VERSION, transport=settings.transport)
    yield AppContext(
        jira_client=jira_client,
        enricher=enricher,
        settings=settings,
        pipeline=pipeline,
        pipeline_runs={},
    )
    logger.info("server_stopped")


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    settings = Settings()
    _configure_logging(settings.log_file)
    ctx_manager = _inspect_lifespan if settings.inspect else _production_lifespan
    async with ctx_manager(server) as ctx:
        yield ctx


_VERSION = importlib.metadata.version("giga-mcp-server")

mcp = FastMCP("giga-mcp-server", lifespan=lifespan, host="0.0.0.0", port=8000)
mcp._mcp_server.version = _VERSION


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


def _app(ctx: Context) -> AppContext:
    return ctx.request_context.lifespan_context


@mcp.tool()
async def get_server_info(ctx: Context = None) -> str:
    """Return server name, version, and runtime configuration."""
    app = _app(ctx)
    s = app.settings
    lines = [
        "**Name:** giga-mcp-server",
        f"**Version:** {_VERSION}",
        f"**Transport:** {s.transport}",
        f"**JIRA URL:** {s.jira_url}",
        f"**JIRA User:** {s.jira_username}",
        f"**JIRA Project:** {s.jira_project_key}",
        f"**GitHub Repo:** {s.github_repo or '(not set)'}",
        f"**GitHub Base Branch:** {s.github_base_branch}",
        f"**AI Model:** {s.anthropic_model}",
        f"**Auth:** {'enabled' if s.auth_enabled else 'disabled'}",
    ]
    return "\n".join(lines)


@mcp.tool()
async def create_story(description: str, auto_enrich: bool = True, ctx: Context = None) -> str:
    """Create a JIRA ticket from a natural language description. AI structures it into a proper story with priority, labels, and acceptance criteria.

    Args:
        description: Natural language description of the feature, bug, or task.
        auto_enrich: If true, automatically enrich the ticket after creation (adds acceptance criteria, subtasks, etc).
    """
    app = _app(ctx)
    result = await app.enricher.create_story(description, auto_enrich=auto_enrich)

    lines = [
        f"## Created {result.jira_key}",
        f"**Summary:** {result.summary}",
        f"**Status:** {result.status}",
        f"**URL:** {result.jira_url}",
    ]
    if auto_enrich:
        lines.append("*Auto-enriched with AI analysis.*")
    return "\n".join(lines)


@mcp.tool()
async def analyze_ticket(issue_key: str, ctx: Context = None) -> str:
    """Analyze a JIRA ticket with AI and preview suggested enrichments. Does NOT modify JIRA.

    Args:
        issue_key: The JIRA issue key, e.g. PIT-42.
    """
    app = _app(ctx)
    analysis = await app.enricher.analyze_ticket(issue_key)

    lines = [
        f"## Analysis for {analysis.issue_key}",
        f"**Priority:** {analysis.suggested_priority}",
        f"**Type:** {analysis.suggested_type}",
        f"**Labels:** {', '.join(analysis.suggested_labels) or 'none'}",
        f"**Should split:** {'Yes' if analysis.should_split else 'No'}",
        f"**Duplicate of:** {analysis.duplicate_of or 'none'}",
        f"**Confidence:** {analysis.confidence:.0%}",
        "",
        "### Acceptance Criteria",
    ]
    for ac in analysis.acceptance_criteria:
        lines.append(f"- {ac}")

    if analysis.subtask_suggestions:
        lines.append("")
        lines.append("### Suggested Subtasks")
        for sub in analysis.subtask_suggestions:
            lines.append(f"- **{sub.summary}**: {sub.description}")

    lines.append("")
    lines.append(f"### Reasoning\n{analysis.reasoning}")
    return "\n".join(lines)


@mcp.tool()
async def enrich_ticket(issue_key: str, ctx: Context = None) -> str:
    """Analyze and apply AI enrichment to a single JIRA ticket. Updates fields, creates subtasks, flags duplicates.

    Args:
        issue_key: The JIRA issue key, e.g. PIT-42.
    """
    app = _app(ctx)
    result = await app.enricher.enrich_ticket(issue_key)

    lines = [f"## Enrichment Result for {result.issue_key}"]
    if result.duplicate_of:
        lines.append(f"Flagged as possible duplicate of **{result.duplicate_of}**.")
    else:
        if result.fields_updated:
            lines.append(f"**Fields updated:** {', '.join(result.fields_updated)}")
        if result.subtasks_created:
            lines.append(f"**Subtasks created:** {', '.join(result.subtasks_created)}")
    lines.append(f"**Comment added:** {'Yes' if result.comment_added else 'No'}")
    return "\n".join(lines)


@mcp.tool()
async def process_backlog(limit: int = 10, ctx: Context = None) -> str:
    """Batch-enrich unprocessed tickets in the backlog.

    Args:
        limit: Maximum number of tickets to process.
    """
    app = _app(ctx)
    results = await app.enricher.process_backlog(limit=limit)

    if not results:
        return "No unprocessed tickets found in the backlog."

    lines = [f"## Processed {len(results)} ticket(s)"]
    for r in results:
        status = f"duplicate of {r.duplicate_of}" if r.duplicate_of else "enriched"
        fields = f" ({', '.join(r.fields_updated)})" if r.fields_updated else ""
        subtasks = f" +{len(r.subtasks_created)} subtasks" if r.subtasks_created else ""
        lines.append(f"- **{r.issue_key}**: {status}{fields}{subtasks}")
    return "\n".join(lines)


@mcp.tool()
async def get_ticket(issue_key: str, ctx: Context = None) -> str:
    """Fetch and display full details of a JIRA ticket.

    Args:
        issue_key: The JIRA issue key, e.g. PIT-42.
    """
    app = _app(ctx)
    t = await app.jira_client.get_issue(issue_key)

    lines = [
        f"## {t['key']}: {t['summary']}",
        f"**Status:** {t['status']}  |  **Priority:** {t['priority']}  |  **Type:** {t['issue_type']}",
        f"**Labels:** {', '.join(t['labels']) or 'none'}",
        f"**Reporter:** {t['reporter']}  |  **Assignee:** {t['assignee'] or 'unassigned'}",
        f"**Created:** {t['created']}  |  **Updated:** {t['updated']}",
        f"**URL:** {t['url']}",
    ]
    if t["parent"]:
        lines.append(f"**Parent:** {t['parent']}")
    if t["subtasks"]:
        lines.append("\n### Subtasks")
        for s in t["subtasks"]:
            lines.append(f"- {s['key']}: {s['summary']}")
    if t["description"]:
        lines.append(f"\n### Description\n{t['description']}")
    return "\n".join(lines)


@mcp.tool()
async def list_backlog(
    limit: int = 20,
    status: str = "To Do",
    unprocessed_only: bool = True,
    ctx: Context = None,
) -> str:
    """List tickets in the project, filtered by status.

    Args:
        limit: Maximum number of tickets to return.
        status: JIRA status to filter by (e.g. 'To Do', 'In Progress', 'Done'). Use 'All' to show all statuses.
        unprocessed_only: If true, only show tickets without the ai-processed label.
    """
    app = _app(ctx)
    s = app.settings
    jql = f'project = "{s.jira_project_key}"'
    if status.lower() != "all":
        jql += f' AND status = "{status}"'
    if unprocessed_only:
        jql += f' AND labels not in ("{s.jira_processed_label}")'
    jql += " ORDER BY created DESC"

    issues = await app.jira_client.search_issues(jql, max_results=limit)
    if not issues:
        return "No tickets found."

    lines = []
    for i in issues:
        lines.append(f"- **{i['key']}** [{i['priority']}] {i['summary']}  \n  {i['url']}")
    return "\n".join(lines)


@mcp.tool()
async def update_ticket_status(issue_key: str, status: str, ctx: Context = None) -> str:
    """Transition a JIRA ticket to a new status (e.g., 'In Progress', 'Done').

    Args:
        issue_key: The JIRA issue key, e.g. PIT-42.
        status: The target status name.
    """
    app = _app(ctx)
    success = await app.jira_client.transition_issue(issue_key, status)
    if success:
        return f"Moved {issue_key} to '{status}'."
    return f"Failed to transition {issue_key} to '{status}'. Check available transitions."


@mcp.tool()
async def find_duplicates(issue_key: str, ctx: Context = None) -> str:
    """Check a JIRA ticket against recent issues for potential duplicates.

    Args:
        issue_key: The JIRA issue key to check, e.g. PIT-42.
    """
    app = _app(ctx)
    matches = await app.enricher.find_duplicates(issue_key)

    if not matches:
        return f"No duplicates found for {issue_key}."

    lines = [f"## Potential duplicates of {issue_key}"]
    for key, ratio in matches:
        lines.append(f"- **{key}**: {ratio:.0%} similarity")
    return "\n".join(lines)


@mcp.tool()
async def process_ticket(
    issue_key: str,
    approve_plan: bool = False,
    ctx: Context = None,
) -> str:
    """Autonomously implement a JIRA ticket: digest → plan → implement → test → PR.

    On the first call (approve_plan=False), runs through the Digester and Planner
    stages, posts the plan as a JIRA comment, then pauses for review.

    On the second call (approve_plan=True), resumes from the approved plan and
    runs the full implementation, validation, and PR creation.

    Args:
        issue_key:    The JIRA issue key to implement, e.g. PIT-42.
        approve_plan: Set True to approve a previously generated plan and proceed
                      with implementation.
    """
    app = _app(ctx)

    if not app.settings.github_token:
        return "Pipeline not configured: GIGA_GITHUB_TOKEN is missing."
    if not app.settings.github_repo:
        return "Pipeline not configured: GIGA_GITHUB_REPO is missing."

    state = app.pipeline_runs.get(issue_key)

    if approve_plan and state and state.status == "awaiting_approval":
        state = await app.pipeline.run_from_plan(issue_key, state)
        app.pipeline_runs[issue_key] = state
    elif state and state.status in ("running", "awaiting_approval"):
        return (
            f"Pipeline for {issue_key} is already {state.status}.\n"
            + state.to_summary()
        )
    else:
        state = PipelineState(ticket_key=issue_key)
        app.pipeline_runs[issue_key] = state
        state = await app.pipeline.run(issue_key, state)
        app.pipeline_runs[issue_key] = state

    return state.to_summary()


@mcp.tool()
async def get_pipeline_status(issue_key: str, ctx: Context = None) -> str:
    """Get the current status of an autonomous pipeline run for a JIRA ticket.

    Args:
        issue_key: The JIRA issue key, e.g. PIT-42.
    """
    app = _app(ctx)
    state = app.pipeline_runs.get(issue_key)
    if not state:
        return f"No pipeline run found for {issue_key}."
    return state.to_summary()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def _configure_auth(settings: Settings) -> None:
    """Configure OAuth token verification if Cognito settings are provided."""
    if not settings.auth_enabled:
        logger.info("auth_disabled", hint="Set GIGA_COGNITO_USER_POOL_ID to enable OAuth")
        return

    from mcp.server.auth.settings import AuthSettings

    from giga_mcp_server.auth import CognitoTokenVerifier

    verifier = CognitoTokenVerifier(
        user_pool_id=settings.cognito_user_pool_id,
        region=settings.cognito_region,
        client_id=settings.cognito_client_id or None,
    )

    issuer_url = (
        f"https://cognito-idp.{settings.cognito_region}.amazonaws.com"
        f"/{settings.cognito_user_pool_id}"
    )

    mcp._token_verifier = verifier
    mcp.settings.auth = AuthSettings(
        issuer_url=issuer_url,
        resource_server_url=settings.public_url or f"https://{settings.host}:{settings.port}",
    )

    logger.info(
        "auth_enabled",
        user_pool_id=settings.cognito_user_pool_id,
        region=settings.cognito_region,
    )


def main() -> None:
    import os as _os

    if "--inspect" in sys.argv:
        _os.environ.setdefault("GIGA_INSPECT", "true")

    settings = Settings()
    _configure_logging(settings.log_file)

    if settings.inspect:
        logger.info("inspect_mode", hint="Running with mock clients")
    if settings.log_file:
        logger.info("logging_to_file", path=settings.log_file)

    if settings.transport == "streamable-http":
        mcp.settings.host = settings.host
        mcp.settings.port = settings.port
        _configure_auth(settings)
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
