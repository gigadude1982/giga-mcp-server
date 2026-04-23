from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Any

import anthropic
import structlog

from giga_mcp_server.config import Settings
from giga_mcp_server.jira.client import JiraClient
from giga_mcp_server.models import (
    EnrichmentResult,
    IdeaResult,
    ParsedIdea,
    SubtaskSuggestion,
    TicketAnalysis,
)

logger = structlog.get_logger()

_DEDUP_THRESHOLD = 0.85

_ANALYSIS_SYSTEM_PROMPT = """\
You are a JIRA ticket analyst. Given a ticket's current data, analyze it and return \
structured JSON to enrich it.

Return ONLY valid JSON (no markdown, no explanation):

{
  "suggested_priority": "One of: Highest, High, Medium, Low, Lowest",
  "suggested_type": "One of: Story, Task, Bug, Epic",
  "suggested_labels": ["lowercase_labels"],
  "acceptance_criteria": ["Given ... When ... Then ...", ...],
  "enriched_description": "Expanded description with context, requirements, and the original description preserved.",
  "should_split": false,
  "subtask_suggestions": [{"summary": "...", "description": "..."}],
  "duplicate_of": null,
  "confidence": 0.85,
  "reasoning": "Brief explanation of the analysis decisions."
}

Guidelines:
- If the ticket mentions a bug, crash, error, or broken feature → suggested_type = "Bug"
- If it's a chore, cleanup, maintenance, or infra → suggested_type = "Task"
- If it's a large feature that should be broken down → should_split = true, provide subtask_suggestions
- Detect urgency from words like "urgent", "critical", "asap", "blocker" → High/Highest
- Default priority is "Medium"
- Infer labels from context (e.g., "login page" → "auth", "API endpoint" → "api")
- Write clear acceptance criteria in Given/When/Then format
- Preserve the original description content in enriched_description, expanding with detail
- If existing_issues are provided, check for semantic duplicates and set duplicate_of to the issue key
- Keep subtask summaries concise and actionable
"""


_STORY_CREATION_PROMPT = """\
You are a JIRA story writer. Given a natural language description of a feature, bug, or task, \
create a well-structured JIRA ticket.

Return ONLY valid JSON (no markdown, no explanation):

{
  "summary": "Concise ticket title (under 80 chars)",
  "description": "Detailed description with context, requirements, and acceptance criteria",
  "priority": "One of: Highest, High, Medium, Low, Lowest",
  "issue_type": "One of: Story, Task, Bug, Epic",
  "labels": ["lowercase_labels"]
}

Guidelines:
- Write a clear, actionable summary (not the raw input — refine it)
- Expand the description with context, steps, and expected behavior
- Include acceptance criteria in the description using Given/When/Then format
- If the input mentions a bug, crash, error, or broken feature → issue_type = "Bug"
- If it's a chore, cleanup, maintenance, or infra → issue_type = "Task"
- Default to "Story" for new features
- Detect urgency from words like "urgent", "critical", "asap" → High/Highest
- Default priority is "Medium"
- Infer labels from context (e.g., "login" → "auth", "API" → "api", "UI" → "frontend")
"""


class TicketEnricher:
    """Analyzes and enriches JIRA tickets using Claude."""

    def __init__(
        self,
        jira_client: JiraClient,
        settings: Settings,
    ) -> None:
        self._jira = jira_client
        self._settings = settings
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._model = settings.anthropic_model

    async def create_story(self, description: str, auto_enrich: bool = True) -> IdeaResult:
        """Parse a natural language description into a JIRA ticket using AI."""
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=4096,
            system=_STORY_CREATION_PROMPT,
            messages=[{"role": "user", "content": description}],
        )
        raw_text = response.content[0].text.strip()
        raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
        raw_text = re.sub(r"\s*```$", "", raw_text)
        data = json.loads(raw_text)

        idea = ParsedIdea(
            summary=data.get("summary", description[:80]),
            description=data.get("description", description),
            priority=data.get("priority", "Medium"),
            issue_type=data.get("issue_type", "Story"),
            labels=data.get("labels", []),
        )

        result = await self._jira.create_story(idea)
        logger.info("story_created", issue_key=result.jira_key, summary=idea.summary)

        if auto_enrich:
            await self.enrich_ticket(result.jira_key)
            logger.info("story_auto_enriched", issue_key=result.jira_key)

        return result

    async def analyze_ticket(self, issue_key: str) -> TicketAnalysis:
        """Fetch a ticket and return AI analysis without modifying JIRA."""
        ticket = await self._jira.get_issue(issue_key)

        # Fetch recent issues for duplicate detection context
        jql = (
            f'project = "{self._settings.jira_project_key}" '
            f"AND key != {issue_key} "
            f"ORDER BY created DESC"
        )
        recent = await self._jira.search_issues(jql, max_results=30)

        user_message = self._build_analysis_prompt(ticket, recent)
        raw = await self._call_claude(user_message)
        return self._parse_analysis(issue_key, raw)

    async def enrich_ticket(
        self,
        issue_key: str,
        analysis: TicketAnalysis | None = None,
    ) -> EnrichmentResult:
        """Analyze (if needed) and apply enrichment to a JIRA ticket."""
        if analysis is None:
            analysis = await self.analyze_ticket(issue_key)

        result = EnrichmentResult(issue_key=issue_key)

        # If it's a duplicate, just flag it and stop
        if analysis.duplicate_of:
            await self._jira.add_comment(
                issue_key,
                f"Possible duplicate of {analysis.duplicate_of}.\n\n"
                f"Confidence: {analysis.confidence:.0%}\n"
                f"Reasoning: {analysis.reasoning}",
            )
            result.duplicate_of = analysis.duplicate_of
            result.comment_added = True
            return result

        # Build field updates
        fields: dict[str, Any] = {}

        if analysis.enriched_description:
            fields["description"] = analysis.enriched_description
            result.fields_updated.append("description")

        if analysis.suggested_priority:
            fields["priority"] = {"name": analysis.suggested_priority}
            result.fields_updated.append("priority")

        if analysis.suggested_type:
            fields["issuetype"] = {"name": analysis.suggested_type}
            result.fields_updated.append("issuetype")

        # Merge labels: keep existing + add suggested + ai-processed marker
        ticket = await self._jira.get_issue(issue_key)
        existing_labels = set(ticket.get("labels", []))
        new_labels = existing_labels | set(analysis.suggested_labels)
        new_labels.add(self._settings.jira_processed_label)
        fields["labels"] = sorted(new_labels)
        result.fields_updated.append("labels")

        if fields:
            await self._jira.update_issue(issue_key, fields)

        # Create subtasks if the ticket should be split
        if analysis.should_split and analysis.subtask_suggestions:
            for sub in analysis.subtask_suggestions:
                sub_result = await self._jira.create_subtask(
                    parent_key=issue_key,
                    summary=sub.summary,
                    description=sub.description,
                )
                result.subtasks_created.append(sub_result.jira_key)

        # Add audit comment
        comment_lines = [f"AI enrichment applied (model: {self._model}):"]
        if result.fields_updated:
            comment_lines.append(f"- Fields updated: {', '.join(result.fields_updated)}")
        if result.subtasks_created:
            comment_lines.append(f"- Subtasks created: {', '.join(result.subtasks_created)}")
        if analysis.acceptance_criteria:
            comment_lines.append("\nAcceptance Criteria:")
            for ac in analysis.acceptance_criteria:
                comment_lines.append(f"- {ac}")
        comment_lines.append(f"\nReasoning: {analysis.reasoning}")

        await self._jira.add_comment(issue_key, "\n".join(comment_lines))
        result.comment_added = True

        logger.info(
            "ticket_enriched",
            issue_key=issue_key,
            fields_updated=result.fields_updated,
            subtasks=result.subtasks_created,
        )
        return result

    async def find_duplicates(
        self, issue_key: str
    ) -> list[tuple[str, float]]:
        """Check a ticket against recent issues for duplicates."""
        ticket = await self._jira.get_issue(issue_key)
        summary = ticket["summary"].lower()

        jql = (
            f'project = "{self._settings.jira_project_key}" '
            f"AND key != {issue_key} "
            f"ORDER BY created DESC"
        )
        recent = await self._jira.search_issues(jql, max_results=50)

        matches = []
        for issue in recent:
            ratio = SequenceMatcher(
                None, summary, issue["summary"].lower()
            ).ratio()
            if ratio >= _DEDUP_THRESHOLD:
                matches.append((issue["key"], ratio))

        matches.sort(key=lambda x: x[1], reverse=True)
        return matches

    async def process_backlog(
        self,
        limit: int = 10,
    ) -> list[EnrichmentResult]:
        """Find and enrich unprocessed backlog tickets."""
        label = self._settings.jira_processed_label
        jql = (
            f'project = "{self._settings.jira_project_key}" '
            f'AND status = "{self._settings.jira_intake_status}" '
            f'AND labels not in ("{label}") '
            f"ORDER BY created ASC"
        )
        tickets = await self._jira.search_issues(jql, max_results=limit)

        results = []
        for ticket in tickets:
            try:
                result = await self.enrich_ticket(ticket["key"])
                results.append(result)
                logger.info("backlog_ticket_processed", issue_key=ticket["key"])
            except Exception:
                logger.exception("backlog_ticket_error", issue_key=ticket["key"])

        return results

    def _build_analysis_prompt(
        self,
        ticket: dict[str, Any],
        recent_issues: list[dict[str, Any]],
    ) -> str:
        parts = [
            f"Ticket: {ticket['key']}",
            f"Summary: {ticket['summary']}",
            f"Description: {ticket['description'] or '(none)'}",
            f"Current Priority: {ticket.get('priority', 'none')}",
            f"Current Type: {ticket.get('issue_type', 'none')}",
            f"Current Labels: {', '.join(ticket.get('labels', [])) or '(none)'}",
        ]

        if recent_issues:
            parts.append("\nExisting issues (check for duplicates):")
            for issue in recent_issues:
                parts.append(f"- {issue['key']}: {issue['summary']}")

        return "\n".join(parts)

    async def _call_claude(self, user_message: str) -> dict[str, Any]:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=4096,
            system=_ANALYSIS_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        raw_text = response.content[0].text.strip()
        # Strip markdown code fences if present
        raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
        raw_text = re.sub(r"\s*```$", "", raw_text)
        return json.loads(raw_text)

    def _parse_analysis(
        self, issue_key: str, data: dict[str, Any]
    ) -> TicketAnalysis:
        subtasks = [
            SubtaskSuggestion(summary=s["summary"], description=s.get("description", ""))
            for s in data.get("subtask_suggestions", [])
        ]
        return TicketAnalysis(
            issue_key=issue_key,
            suggested_priority=data.get("suggested_priority", "Medium"),
            suggested_type=data.get("suggested_type", "Story"),
            suggested_labels=data.get("suggested_labels", []),
            acceptance_criteria=data.get("acceptance_criteria", []),
            enriched_description=data.get("enriched_description", ""),
            should_split=data.get("should_split", False),
            subtask_suggestions=subtasks,
            duplicate_of=data.get("duplicate_of"),
            confidence=data.get("confidence", 0.0),
            reasoning=data.get("reasoning", ""),
        )
