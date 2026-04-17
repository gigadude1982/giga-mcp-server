from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class WhatsAppMessage:
    id: str
    chat_jid: str
    sender: str
    content: str
    timestamp: datetime
    is_from_me: bool


@dataclass
class ParsedIdea:
    summary: str
    description: str
    priority: str = "Medium"  # Highest, High, Medium, Low, Lowest
    labels: list[str] = field(default_factory=list)
    issue_type: str = "Story"  # Story, Task, Bug
    components: list[str] = field(default_factory=list)
    raw_message: str = ""
    sender: str = ""
    timestamp: datetime | None = None


@dataclass
class IdeaResult:
    jira_key: str
    jira_url: str
    summary: str
    status: str
