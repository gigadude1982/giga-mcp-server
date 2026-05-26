from __future__ import annotations

import asyncio
from typing import Any

import structlog

from giga_mcp_server.pipeline.agent_runner import AgentRunner
from giga_mcp_server.pipeline.github_tools import GitHubClient
from giga_mcp_server.vector.code_history import CodeHistoryStore

logger = structlog.get_logger()


class CodeHistoryIngester:
    """Indexes merged PRs into the code-history vector store.

    Each PR is summarised by a Haiku-class agent into 3-5 dense sentences
    before embedding — raw diffs are too noisy and too large to embed
    directly. The summarizer agent (`pr_summarizer` in AGENT_REGISTRY)
    enforces a strict JSON output schema, giving us schema-validated input
    to the vector store.

    Idempotent on PR number — the underlying VectorStore.upsert overwrites
    existing records with the same id, so re-running backfill is safe.
    """

    def __init__(
        self,
        github: GitHubClient,
        store: CodeHistoryStore,
        summarizer_runner: AgentRunner,
        base_branch: str = "main",
        concurrency: int = 5,
    ) -> None:
        self._github = github
        self._store = store
        self._runner = summarizer_runner
        self._base_branch = base_branch
        self._sem = asyncio.Semaphore(concurrency)

    async def backfill(
        self, since_days: int = 90, limit: int = 200
    ) -> dict[str, int]:
        """Index all merged PRs against the base branch within the window."""
        prs = await self._github.list_merged_prs(
            since_days=since_days,
            base_branch=self._base_branch,
            limit=limit,
        )
        if not prs:
            logger.info("code_history_backfill_empty")
            return {"discovered": 0, "indexed": 0, "skipped": 0}

        results = await asyncio.gather(
            *(self._index_one(pr) for pr in prs),
            return_exceptions=False,
        )
        indexed = sum(1 for r in results if r)
        skipped = len(results) - indexed
        logger.info(
            "code_history_backfill_complete",
            discovered=len(prs),
            indexed=indexed,
            skipped=skipped,
        )
        return {"discovered": len(prs), "indexed": indexed, "skipped": skipped}

    async def index_pr(self, pr_number: int) -> bool:
        """Index a single PR by number. Returns False for unmerged PRs."""
        pr = await self._github.get_pr(pr_number)
        if not pr.get("merged"):
            logger.info("code_history_skip_unmerged", pr=pr_number)
            return False
        return await self._index_one(pr)

    async def _index_one(self, pr: dict[str, Any]) -> bool:
        async with self._sem:
            try:
                summarized = await self._runner.run(
                    "pr_summarizer",
                    {
                        "title": pr.get("title", ""),
                        "body": pr.get("body", "") or "",
                        "files": pr.get("files", []),
                        "merged_at": pr.get("merged_at", ""),
                    },
                )
                await self._store.upsert_pr(
                    pr_number=pr["number"],
                    summary_text=summarized["summary"],
                    files=pr.get("files", []),
                    title=pr.get("title", ""),
                    merged_at=pr.get("merged_at", ""),
                    sha=pr.get("merge_commit_sha", ""),
                    ticket_key=summarized.get("ticket_key", ""),
                )
                logger.info(
                    "code_history_indexed",
                    pr=pr["number"],
                    files=len(pr.get("files", [])),
                )
                return True
            except Exception as e:
                logger.warning(
                    "code_history_index_failed",
                    pr=pr.get("number"),
                    error=str(e),
                )
                return False
