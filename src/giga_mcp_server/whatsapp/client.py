from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite
import httpx
import structlog

from giga_mcp_server.models import WhatsAppMessage

logger = structlog.get_logger()


class WhatsAppClient:
    """Reads messages from the whatsapp-mcp SQLite DB and sends via the Go bridge HTTP API."""

    def __init__(self, db_path: str, bridge_url: str) -> None:
        self._db_path = db_path
        self._bridge_url = bridge_url.rstrip("/")

    async def get_new_messages(
        self,
        since: datetime,
        chat_jid: str | None = None,
    ) -> list[WhatsAppMessage]:
        """Fetch messages newer than `since` from the whatsapp-mcp SQLite database.

        Args:
            since: Only return messages after this timestamp (UTC).
            chat_jid: If provided, filter to this specific chat.

        Returns:
            List of messages ordered by timestamp ascending.
        """
        query = """
            SELECT id, chat_jid, sender, content, timestamp, is_from_me
            FROM messages
            WHERE timestamp > ?
        """
        params: list[object] = [since.timestamp()]

        if chat_jid:
            query += " AND chat_jid = ?"
            params.append(chat_jid)

        query += " ORDER BY timestamp ASC"

        messages: list[WhatsAppMessage] = []
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(query, params) as cursor:
                async for row in cursor:
                    messages.append(
                        WhatsAppMessage(
                            id=str(row[0]),
                            chat_jid=row[1],
                            sender=row[2] or "",
                            content=row[3] or "",
                            timestamp=datetime.fromtimestamp(row[4], tz=timezone.utc),
                            is_from_me=bool(row[5]),
                        )
                    )

        logger.debug("fetched_messages", count=len(messages), since=since.isoformat())
        return messages

    async def send_message(self, jid: str, text: str) -> bool:
        """Send a text message via the Go bridge HTTP API.

        Args:
            jid: Recipient JID (group or individual).
            text: Message text to send.

        Returns:
            True if the message was sent successfully.
        """
        url = f"{self._bridge_url}/api/send"
        payload = {"recipient": jid, "message": text}

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                success = data.get("success", False)
                if not success:
                    logger.warning("send_message_failed", jid=jid, response=data)
                return success
        except httpx.HTTPError as exc:
            logger.error("send_message_error", jid=jid, error=str(exc))
            return False
