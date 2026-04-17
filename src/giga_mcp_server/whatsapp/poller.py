from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from giga_mcp_server.pipeline import IdeaPipeline
    from giga_mcp_server.whatsapp.client import WhatsAppClient

logger = structlog.get_logger()

_WATERMARK_FILE = ".giga_watermark"


class Poller:
    """Background task that polls WhatsApp SQLite for new group messages."""

    def __init__(
        self,
        wa_client: WhatsAppClient,
        pipeline: IdeaPipeline,
        group_jid: str,
        poll_interval: int = 10,
        watermark_path: str = _WATERMARK_FILE,
    ) -> None:
        self._wa_client = wa_client
        self._pipeline = pipeline
        self._group_jid = group_jid
        self._poll_interval = poll_interval
        self._watermark_path = Path(watermark_path)
        self._last_seen = self._load_watermark()
        self._processed_count = 0
        self._error_count = 0
        self._last_poll_time: datetime | None = None

    @property
    def stats(self) -> dict:
        return {
            "last_poll_time": self._last_poll_time.isoformat() if self._last_poll_time else None,
            "last_seen_timestamp": self._last_seen.isoformat(),
            "processed_count": self._processed_count,
            "error_count": self._error_count,
            "group_jid": self._group_jid,
            "poll_interval_seconds": self._poll_interval,
        }

    async def run(self) -> None:
        """Poll indefinitely. Designed to run as an asyncio.Task."""
        logger.info("poller_started", group_jid=self._group_jid, interval=self._poll_interval)
        while True:
            try:
                await self._poll_once()
            except Exception:
                self._error_count += 1
                logger.exception("poller_error")
            await asyncio.sleep(self._poll_interval)

    async def _poll_once(self) -> None:
        self._last_poll_time = datetime.now(timezone.utc)
        messages = await self._wa_client.get_new_messages(
            since=self._last_seen,
            chat_jid=self._group_jid,
        )

        for msg in messages:
            # Skip our own messages (confirmations we sent)
            if msg.is_from_me:
                continue
            # Skip empty messages
            if not msg.content.strip():
                continue

            try:
                result = await self._pipeline.process_message(msg)
                self._processed_count += 1
                logger.info(
                    "message_processed",
                    jira_key=result.jira_key,
                    sender=msg.sender,
                )
            except Exception:
                self._error_count += 1
                logger.exception("pipeline_error", message_id=msg.id)

            # Advance watermark past this message regardless of success/failure
            if msg.timestamp > self._last_seen:
                self._last_seen = msg.timestamp
                self._save_watermark()

    def _load_watermark(self) -> datetime:
        if self._watermark_path.exists():
            try:
                data = json.loads(self._watermark_path.read_text())
                return datetime.fromisoformat(data["last_seen"])
            except (json.JSONDecodeError, KeyError, ValueError):
                logger.warning("watermark_corrupt", path=str(self._watermark_path))
        return datetime.now(timezone.utc)

    def _save_watermark(self) -> None:
        self._watermark_path.write_text(
            json.dumps({"last_seen": self._last_seen.isoformat()})
        )
