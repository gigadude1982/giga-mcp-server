from __future__ import annotations

import asyncio
from typing import Any

import structlog
from pinecone import Pinecone

logger = structlog.get_logger()

_NAMESPACE = "__default__"
_TEXT_FIELD = "text"


class VectorStore:
    """Pinecone integrated-inference store for ticket similarity search.

    The index must already exist in Pinecone with an embedded model configured
    (e.g. multilingual-e5-large). Upserts and queries send raw text; Pinecone
    handles embedding internally — no separate embedding client needed.
    """

    def __init__(self, api_key: str, index_name: str) -> None:
        self._pc = Pinecone(api_key=api_key)
        self._index_name = index_name
        self._index = None

    async def setup(self) -> None:
        """Verify the index exists and connect to it. Call once at startup."""
        await asyncio.to_thread(self._pc.describe_index, self._index_name)
        self._index = self._pc.Index(self._index_name)
        logger.info("vector_store_ready", index=self._index_name)

    async def upsert(self, key: str, text: str, metadata: dict[str, Any]) -> None:
        record = {"id": key, _TEXT_FIELD: text, **metadata}
        await asyncio.to_thread(
            self._index.upsert_records, _NAMESPACE, [record]
        )
        logger.debug("vector_upserted", key=key)

    async def search(
        self,
        query_text: str,
        limit: int = 5,
        required_label: str | None = None,
    ) -> list[dict[str, Any]]:
        query: dict[str, Any] = {"inputs": {_TEXT_FIELD: query_text}, "top_k": limit}
        if required_label:
            query["filter"] = {"labels": {"$in": [required_label]}}
        results = await asyncio.to_thread(
            self._index.search_records, _NAMESPACE, query
        )
        return [{**hit.fields, "_score": hit._score} for hit in results.result.hits]

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(
            self._index.delete, ids=[key], namespace=_NAMESPACE
        )
