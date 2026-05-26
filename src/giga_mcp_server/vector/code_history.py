from __future__ import annotations

from typing import Any

import structlog

from giga_mcp_server.vector.store import VectorStore

logger = structlog.get_logger()


class CodeHistoryStore:
    """Long-term semantic memory of merged PRs and code-change outcomes.

    Records are LLM-generated 3-5 sentence summaries of merged PRs, embedded by
    Pinecone integrated inference. The Implementer and Validator agents query
    this store at pipeline runtime to ground generation in how this codebase
    has actually evolved — patterns introduced, deprecated approaches, prior
    fixes for similar work.

    Backed by VectorStore pointed at a separate Pinecone index from the ticket
    store. Records carry a `kind` field ("commit" today; "pr_review" /
    "ci_failure" reserved for future expansion) so a single index can serve
    multiple agent retrieval shapes without migration.
    """

    def __init__(self, store: VectorStore) -> None:
        self._store = store

    async def setup(self) -> None:
        await self._store.setup()

    async def upsert_pr(
        self,
        pr_number: int,
        summary_text: str,
        files: list[str],
        title: str,
        merged_at: str,
        sha: str,
        ticket_key: str = "",
    ) -> None:
        """Idempotently upsert a merged PR's summary."""
        await self._store.upsert(
            key=f"pr-{pr_number}",
            text=summary_text,
            metadata={
                "kind": "commit",
                "pr_number": pr_number,
                "title": title,
                "files": files,
                "merged_at": merged_at,
                "sha": sha,
                "ticket_key": ticket_key,
            },
        )

    async def search_similar(
        self,
        query_text: str,
        limit: int = 5,
        kind: str | None = None,
        file_path: str | None = None,
    ) -> list[dict[str, Any]]:
        """Vector-search for relevant historical PRs.

        kind:      Restrict to records of this kind (e.g. "commit").
        file_path: Restrict to records that touched this file path.
        """
        clauses: list[dict[str, Any]] = []
        if kind:
            clauses.append({"kind": {"$eq": kind}})
        if file_path:
            clauses.append({"files": {"$in": [file_path]}})

        metadata_filter: dict[str, Any] | None
        if not clauses:
            metadata_filter = None
        elif len(clauses) == 1:
            metadata_filter = clauses[0]
        else:
            metadata_filter = {"$and": clauses}

        return await self._store.search(
            query_text=query_text,
            limit=limit,
            metadata_filter=metadata_filter,
        )
