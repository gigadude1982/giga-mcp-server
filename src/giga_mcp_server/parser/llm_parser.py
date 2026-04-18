from __future__ import annotations

import json
import logging
from datetime import datetime

import anthropic

from giga_mcp_server.models import ParsedIdea
from giga_mcp_server.parser.base import MessageParser

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a message parser that converts informal WhatsApp messages into structured JIRA issue data.

Given a raw message, extract the following fields and return ONLY valid JSON (no markdown, no explanation):

{
  "summary": "Short, actionable title (max 120 chars). Clean up grammar but keep the intent.",
  "description": "Full description with context. Include the original message verbatim at the end.",
  "priority": "One of: Highest, High, Medium, Low, Lowest",
  "labels": ["lowercase_labels", "extracted_from_hashtags_or_context"],
  "issue_type": "One of: Story, Task, Bug"
}

Guidelines:
- If the message mentions a bug, crash, error, broken feature → issue_type = "Bug"
- If it's a chore, cleanup, or maintenance → issue_type = "Task"
- Otherwise → issue_type = "Story"
- Detect urgency from words like "urgent", "critical", "asap", "blocker" → High/Highest
- Detect low priority from "nice to have", "low priority", "when you get a chance" → Low/Lowest
- Default priority is "Medium"
- Extract #hashtags as labels, and infer additional labels from context (e.g., "login page" → "auth")
- Keep the summary concise and professional
"""


class LLMParser(MessageParser):
    """Parses WhatsApp messages into JIRA ideas using Claude."""

    def __init__(self, api_key: str | None = None, model: str = "claude-haiku-4-5-20251001") -> None:
        if not api_key:
            raise ValueError("GIGA_ANTHROPIC_API_KEY is required for the LLM parser")
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        # Lazy import for fallback
        from giga_mcp_server.parser.rule_based import RuleBasedParser

        self._fallback = RuleBasedParser()

    def parse(
        self,
        content: str,
        sender: str,
        timestamp: datetime | None = None,
    ) -> ParsedIdea:
        if not content.strip():
            return self._fallback.parse(content, sender, timestamp)

        try:
            return self._call_llm(content, sender, timestamp)
        except Exception:
            logger.exception("llm_parser_failed, falling back to rule-based")
            return self._fallback.parse(content, sender, timestamp)

    def _call_llm(
        self,
        content: str,
        sender: str,
        timestamp: datetime | None,
    ) -> ParsedIdea:
        user_message = f"Sender: {sender}\nMessage: {content}"

        response = self._client.messages.create(
            model=self._model,
            max_tokens=512,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )

        raw_text = response.content[0].text.strip()
        data = json.loads(raw_text)

        return ParsedIdea(
            summary=data.get("summary", content[:120])[:120],
            description=data.get("description", content),
            priority=data.get("priority", ""),
            labels=[tag.lower() for tag in data.get("labels", [])],
            issue_type=data.get("issue_type", ""),
            raw_message=content,
            sender=sender,
            timestamp=timestamp,
        )
