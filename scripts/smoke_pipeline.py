#!/usr/bin/env python3
"""Watched local smoke run of the autonomous pipeline against a REAL board.

Runs THIS worktree's code end-to-end (digester → planner → implementer/tests →
pre-flight validator → draft PR → real GitHub Actions CI gate → mark ready)
for a single JIRA ticket, streaming each stage transition to the console.

It hits real JIRA + GitHub + Anthropic and WILL create a branch and a draft PR
on the target repo. Code-history (Pinecone) is intentionally left disabled to
keep the run lean — the pipeline treats it as opt-in and runs identically
without it.

Usage (source a board env first so GIGA_* vars are present):

    set -a; source .env.gigacorp-react; set +a
    uv run python scripts/smoke_pipeline.py GIGA-123

    # keep the human gate (two-call flow): plan only, don't implement
    uv run python scripts/smoke_pipeline.py GIGA-123 --gate

    # force reprocessing a ticket already in a terminal status
    uv run python scripts/smoke_pipeline.py GIGA-123 --force
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

import structlog

from giga_mcp_server.config import Settings
from giga_mcp_server.jira.client import JiraClient
from giga_mcp_server.pipeline.orchestrator import PipelineOrchestrator, PipelineState


def _use_system_trust() -> None:
    """Route TLS verification through the OS native trust store when truststore
    is available. On corporate networks with a TLS-intercepting proxy (e.g.
    Zscaler), the re-signed cert chain is trusted by the OS keychain but not by
    Python's bundled certifi — and OpenSSL 3 rejects appending it to a PEM
    bundle. truststore sidesteps both. No-op (plain certifi) if not installed."""
    try:
        import truststore

        truststore.inject_into_ssl()
    except ImportError:
        pass


def _configure_logging() -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stderr, level=logging.INFO)
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="%H:%M:%S"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ticket_key", help="JIRA ticket key, e.g. GIGA-123")
    parser.add_argument(
        "--gate", action="store_true",
        help="Honor the human gate: run digester+planner only, then stop at awaiting_approval.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Reprocess even if the ticket is in a terminal JIRA status.",
    )
    args = parser.parse_args()

    settings = Settings()
    settings.validate_required()

    log = structlog.get_logger()
    log.info(
        "smoke_run_start",
        ticket=args.ticket_key,
        repo=settings.github_repo,
        project=settings.jira_project_key,
        skip_human_gate=not args.gate,
    )

    jira_client = JiraClient(settings)
    # code_history left None on purpose — it is opt-in and the pipeline runs
    # identically without it; skipping it avoids a Pinecone round-trip.
    pipeline = PipelineOrchestrator(settings, jira_client, code_history=None)

    state = PipelineState(ticket_key=args.ticket_key)
    await pipeline.run(args.ticket_key, state, skip_human_gate=not args.gate)

    print("\n" + "=" * 60, file=sys.stderr)
    print("FINAL PIPELINE STATE", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(state.to_summary(), file=sys.stderr)
    return 0 if state.status in ("done", "awaiting_approval") else 1


if __name__ == "__main__":
    _use_system_trust()
    _configure_logging()
    raise SystemExit(asyncio.run(_main()))
