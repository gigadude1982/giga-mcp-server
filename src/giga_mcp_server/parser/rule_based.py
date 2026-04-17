from __future__ import annotations

import re
from datetime import datetime

from giga_mcp_server.models import ParsedIdea
from giga_mcp_server.parser.base import MessageParser

# Priority keyword mappings (checked in order, first match wins)
_PRIORITY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(critical|blocker|p0)\b", re.IGNORECASE), "Highest"),
    (re.compile(r"\b(urgent|asap|high\s*priority|p1)\b", re.IGNORECASE), "High"),
    (re.compile(r"\b(low\s*priority|minor|p3|nice\s*to\s*have)\b", re.IGNORECASE), "Low"),
    (re.compile(r"\b(trivial|p4)\b", re.IGNORECASE), "Lowest"),
]

# Issue type keyword mappings
_TYPE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(bug|fix|broken|crash|error|regression)\b", re.IGNORECASE), "Bug"),
    (re.compile(r"\b(task|todo|chore|cleanup)\b", re.IGNORECASE), "Task"),
    # Story is the default — matched last or by fallthrough
    (re.compile(r"\b(idea|feature|build|create|add|implement|design)\b", re.IGNORECASE), "Story"),
]

_HASHTAG_PATTERN = re.compile(r"#(\w+)")
_SENTENCE_END = re.compile(r"[.!?\n]")

_MAX_SUMMARY_LENGTH = 120


class RuleBasedParser(MessageParser):
    """Parses WhatsApp messages into JIRA ideas using keyword extraction."""

    def parse(
        self,
        content: str,
        sender: str,
        timestamp: datetime | None = None,
    ) -> ParsedIdea:
        content = content.strip()
        if not content:
            return ParsedIdea(
                summary="Empty message",
                description="(no content)",
                sender=sender,
                raw_message=content,
                timestamp=timestamp,
            )

        return ParsedIdea(
            summary=self._extract_summary(content),
            description=self._build_description(content, sender),
            priority=self._extract_priority(content),
            labels=self._extract_labels(content),
            issue_type=self._extract_issue_type(content),
            raw_message=content,
            sender=sender,
            timestamp=timestamp,
        )

    def _extract_summary(self, content: str) -> str:
        # Strip hashtags for summary
        clean = _HASHTAG_PATTERN.sub("", content).strip()
        # Take the first sentence
        match = _SENTENCE_END.search(clean)
        if match and match.start() > 0:
            summary = clean[: match.start()].strip()
        else:
            summary = clean

        if len(summary) > _MAX_SUMMARY_LENGTH:
            summary = summary[: _MAX_SUMMARY_LENGTH - 3].rsplit(" ", 1)[0] + "..."
        return summary

    def _build_description(self, content: str, sender: str) -> str:
        lines = [content, "", "---", f"Submitted via WhatsApp by {sender}"]
        return "\n".join(lines)

    def _extract_priority(self, content: str) -> str:
        for pattern, priority in _PRIORITY_PATTERNS:
            if pattern.search(content):
                return priority
        return "Medium"

    def _extract_labels(self, content: str) -> list[str]:
        return [tag.lower() for tag in _HASHTAG_PATTERN.findall(content)]

    def _extract_issue_type(self, content: str) -> str:
        for pattern, issue_type in _TYPE_PATTERNS:
            if pattern.search(content):
                return issue_type
        return "Story"
