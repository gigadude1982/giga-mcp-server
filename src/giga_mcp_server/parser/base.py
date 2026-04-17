from __future__ import annotations

from abc import ABC, abstractmethod

from giga_mcp_server.models import ParsedIdea


class MessageParser(ABC):
    """Abstract interface for parsing WhatsApp messages into structured JIRA ideas."""

    @abstractmethod
    def parse(self, content: str, sender: str, timestamp: object = None) -> ParsedIdea:
        """Parse a raw WhatsApp message into a structured idea.

        Args:
            content: The message text.
            sender: The sender identifier (phone or name).
            timestamp: When the message was sent.

        Returns:
            A ParsedIdea ready for JIRA issue creation.
        """
