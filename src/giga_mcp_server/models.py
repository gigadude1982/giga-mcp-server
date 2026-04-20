from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ParsedIdea:
    summary: str
    description: str
    priority: str = ""
    labels: list[str] = field(default_factory=list)
    issue_type: str = ""
    components: list[str] = field(default_factory=list)


@dataclass
class IdeaResult:
    jira_key: str
    jira_url: str
    summary: str
    status: str


@dataclass
class SubtaskSuggestion:
    summary: str
    description: str


@dataclass
class TicketAnalysis:
    """Result of AI analysis of a single JIRA ticket."""

    issue_key: str
    suggested_priority: str
    suggested_type: str
    suggested_labels: list[str]
    acceptance_criteria: list[str]
    enriched_description: str
    should_split: bool
    subtask_suggestions: list[SubtaskSuggestion] = field(default_factory=list)
    duplicate_of: str | None = None
    confidence: float = 0.0
    reasoning: str = ""


@dataclass
class EnrichmentResult:
    """Result of applying enrichment to a JIRA ticket."""

    issue_key: str
    fields_updated: list[str] = field(default_factory=list)
    subtasks_created: list[str] = field(default_factory=list)
    duplicate_of: str | None = None
    comment_added: bool = False
