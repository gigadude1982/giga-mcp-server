from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest


@pytest.fixture
def tmp_whatsapp_db(tmp_path: Path) -> Path:
    """Create a pre-seeded WhatsApp SQLite database matching whatsapp-mcp schema."""
    db_path = tmp_path / "messages.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            chat_jid TEXT,
            sender TEXT,
            content TEXT,
            timestamp REAL,
            is_from_me INTEGER,
            media_type TEXT,
            filename TEXT,
            url TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chats (
            jid TEXT PRIMARY KEY,
            name TEXT,
            last_message_time REAL
        )
    """)

    group_jid = "120363001234567890@g.us"
    base_ts = datetime(2026, 4, 16, 12, 0, 0, tzinfo=timezone.utc).timestamp()

    messages = [
        ("msg1", group_jid, "+15551234567", "Build a dashboard for user signups #frontend", base_ts + 1, 0),
        ("msg2", group_jid, "+15559876543", "urgent: fix login bug on mobile app", base_ts + 60, 0),
        ("msg3", group_jid, "+15551234567", "Add search feature to the admin panel #backend #search", base_ts + 120, 0),
        ("msg4", group_jid, "me", "✅ Created PROJ-100: Build a dashboard for user signups", base_ts + 5, 1),
    ]
    conn.executemany(
        "INSERT INTO messages (id, chat_jid, sender, content, timestamp, is_from_me) VALUES (?, ?, ?, ?, ?, ?)",
        messages,
    )
    conn.execute(
        "INSERT INTO chats (jid, name, last_message_time) VALUES (?, ?, ?)",
        (group_jid, "Ideas Group", base_ts + 120),
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def group_jid() -> str:
    return "120363001234567890@g.us"
