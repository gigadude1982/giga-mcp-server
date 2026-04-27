from __future__ import annotations

from typing import Any

from giga_mcp_server.jira.client import JiraClient


def extract_adf_text(description: Any) -> str:
    """Extract plain text from an Atlassian Document Format (ADF) description.

    JIRA Cloud returns descriptions as ADF objects. This walks the node tree
    and concatenates text nodes.
    """
    if description is None:
        return ""
    if isinstance(description, str):
        return description

    # ADF is a nested dict with a "content" list of nodes
    if isinstance(description, dict):
        node_type = description.get("type", "")
        text = description.get("text", "")

        if text:
            return text

        parts = []
        for child in description.get("content", []):
            child_text = extract_adf_text(child)
            if child_text:
                parts.append(child_text)

        separator = "\n" if node_type in ("paragraph", "heading", "bulletList", "orderedList") else " "
        return separator.join(parts)

    return ""


async def get_ticket_for_pipeline(jira_client: JiraClient, ticket_key: str) -> dict[str, Any]:
    """Fetch a JIRA ticket and return a clean dict suitable for the Digester agent input.

    Includes user comments so the digester can incorporate additional context and
    direction left by a human reviewer on the ticket.
    """
    raw = await jira_client.get_issue(ticket_key)

    description = raw.get("description", "")
    if isinstance(description, dict):
        description = extract_adf_text(description)

    comments = await jira_client.get_comments(ticket_key)

    return {
        "ticket_key": raw["key"],
        "summary": raw.get("summary", ""),
        "description": description,
        "comments": comments,
        "issue_type": raw.get("issue_type", "Story"),
        "priority": raw.get("priority", "Medium"),
        "labels": raw.get("labels", []),
    }


async def transition_ticket(
    jira_client: JiraClient,
    ticket_key: str,
    status: str,
) -> bool:
    """Transition a JIRA ticket to a new status. Returns True on success."""
    return await jira_client.transition_issue(ticket_key, status)


async def add_pipeline_comment(
    jira_client: JiraClient,
    ticket_key: str,
    comment: str,
) -> bool:
    """Post a comment on a JIRA ticket from the pipeline."""
    return await jira_client.add_comment(ticket_key, comment)
