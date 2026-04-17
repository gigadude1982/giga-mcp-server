from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from giga_mcp_server.whatsapp.client import WhatsAppClient


class TestWhatsAppClientRead:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_whatsapp_db: Path, group_jid: str) -> None:
        self.client = WhatsAppClient(db_path=str(tmp_whatsapp_db), bridge_url="http://localhost:8080")
        self.group_jid = group_jid

    async def test_get_new_messages_returns_all(self) -> None:
        since = datetime(2026, 4, 16, 11, 0, 0, tzinfo=timezone.utc)
        messages = await self.client.get_new_messages(since=since)
        assert len(messages) == 4  # All messages including is_from_me

    async def test_get_new_messages_filtered_by_jid(self) -> None:
        since = datetime(2026, 4, 16, 11, 0, 0, tzinfo=timezone.utc)
        messages = await self.client.get_new_messages(since=since, chat_jid=self.group_jid)
        assert len(messages) == 4

    async def test_get_new_messages_respects_since(self) -> None:
        # Set since to after the first two messages
        since = datetime(2026, 4, 16, 12, 0, 30, tzinfo=timezone.utc)
        messages = await self.client.get_new_messages(since=since, chat_jid=self.group_jid)
        # Should get msg2 (base+60) and msg3 (base+120), not msg1 (base+1) or msg4 (base+5)
        assert len(messages) == 2

    async def test_messages_ordered_by_timestamp(self) -> None:
        since = datetime(2026, 4, 16, 11, 0, 0, tzinfo=timezone.utc)
        messages = await self.client.get_new_messages(since=since)
        timestamps = [m.timestamp for m in messages]
        assert timestamps == sorted(timestamps)

    async def test_is_from_me_flag(self) -> None:
        since = datetime(2026, 4, 16, 11, 0, 0, tzinfo=timezone.utc)
        messages = await self.client.get_new_messages(since=since)
        from_me = [m for m in messages if m.is_from_me]
        assert len(from_me) == 1
        assert "Created PROJ-100" in from_me[0].content
