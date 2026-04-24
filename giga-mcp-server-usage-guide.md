# GigaCorp MCP Server — Claude AI Usage Guide

## Overview

The GigaCorp MCP Server connects Claude AI to the Pitch Vault Jira board and GitHub
repository, enabling autonomous ticket management and full pipeline execution directly
from a Claude chat session.

**Server:** `https://mcp.gigacorp.co/mcp`
**Jira Project:** `PIT` (pitchvault.atlassian.net)
**GitHub Repo:** `gigadude1982/pitchvault-react`
**Base Branch:** `main`

---

## Connecting the Server

1. In Claude.ai, go to **Settings → Connectors**
2. Add the GigaCorp MCP Server using the URL above
3. Start a new conversation — tools will be available immediately

> **Note:** If you add new tools to the server and they don't appear in an active
> session, disconnect and reconnect the connector in Settings, then start a fresh chat.
> Claude cannot pick up new tool definitions mid-conversation without a reconnect.

---

## Available Tools

### `get_server_info`
Returns the server name, version, transport, Jira config, GitHub config, and AI model.
Useful for confirming the server is reachable and checking the current version.

```
get_server_info()
```

---

### `create_story`
Creates a new Jira ticket from a natural language description.

**Important:** Always use `auto_enrich: false`. The `auto_enrich: true` option causes
JSON parse errors and is unreliable.

```
create_story(
  description: "Your story description here",
  auto_enrich: false
)
```

Returns the new ticket key (e.g. `PIT-42`) and a link to the Jira issue.

---

### `get_ticket`
Fetches full details of a Jira ticket including status, priority, labels, and description.

```
get_ticket(issue_key: "PIT-42")
```

---

### `list_backlog`
Lists tickets in the backlog, optionally filtered by status or unprocessed state.

```
list_backlog(
  status: "To Do",         // e.g. "To Do", "In Progress", "Done", "All"
  unprocessed_only: true,  // only show tickets without the ai-processed label
  limit: 20
)
```

---

### `update_ticket_status`
Transitions a Jira ticket to a new status.

```
update_ticket_status(
  issue_key: "PIT-42",
  status: "In Progress"   // e.g. "To Do", "In Progress", "Done", "Obsolete"
)
```

---

### `enrich_ticket`
Runs AI enrichment on a ticket to update its description, priority, issue type, and
labels. Adds a Jira comment recording the enrichment.

> **Note:** This tool is separate from the autonomous pipeline. Use it to improve ticket
> metadata without triggering a full implementation run.

```
enrich_ticket(issue_key: "PIT-42")
```

---

### `analyze_ticket`
Analyzes a ticket and previews suggested enrichments without applying them.

```
analyze_ticket(issue_key: "PIT-42")
```

---

### `find_duplicates`
Checks a ticket against recent issues for potential duplicates.

```
find_duplicates(issue_key: "PIT-42")
```

---

### `process_ticket` ⭐
**The main tool for autonomous implementation.** Runs the full pipeline:
`digest → plan → implement → test → PR`

The pipeline is **asynchronous and non-blocking** — `process_ticket` returns immediately
and the pipeline runs in the background. Use `get_pipeline_status` to poll for progress.

#### First call — generate a plan (default)
```
process_ticket(issue_key: "PIT-42")
```
Runs the Digester and Planner stages, posts the plan as a Jira comment, then pauses
for human review. Check Jira for the plan comment before approving.

#### Second call — approve and implement
```
process_ticket(
  issue_key: "PIT-42",
  approve_plan: true
)
```
Resumes from the approved plan and runs the full implementation, validation, and PR
creation.

#### Force reprocess (e.g. after closing a PR or resetting a ticket)
```
process_ticket(
  issue_key: "PIT-42",
  force: true
)
```
Creates a new branch and PR even if the ticket was previously implemented. Combine
with `approve_plan: true` to skip the human gate and run the entire pipeline end-to-end
in a single call.

> **Before force reprocessing:** Move the ticket back to "To Do" in Jira and delete
> the corresponding branch and PR in GitHub so the pipeline starts clean.

---

### `get_pipeline_status`
Polls the status of an in-progress autonomous pipeline run.

```
get_pipeline_status(issue_key: "PIT-42")
```

Returns the current stage (e.g. `planner`, `implementer`, `pr_creator`) and overall
status (`running`, `awaiting_approval`, `done`, `failed`).

**Polling pattern:** After calling `process_ticket`, poll every 10–15 seconds until
the status transitions out of `running`. Claude will do this automatically when asked
to process a ticket.

---

### `process_backlog`
Batch-enriches multiple unprocessed tickets in the backlog at once.

```
process_backlog(limit: 10)
```

---

## Common Workflows

### Create and process a new ticket end-to-end

1. `create_story(description: "...", auto_enrich: false)` → get ticket key
2. `process_ticket(issue_key: "PIT-XX")` → plan is generated and posted to Jira
3. Review the plan comment in Jira
4. `process_ticket(issue_key: "PIT-XX", approve_plan: true)` → implementation runs
5. `get_pipeline_status(issue_key: "PIT-XX")` → poll until done
6. Review the PR in GitHub

### Reprocess a ticket from scratch

1. Move ticket back to "To Do" in Jira
2. Delete the existing branch and PR in GitHub
3. `process_ticket(issue_key: "PIT-XX", force: true)` → pipeline starts fresh
4. Poll with `get_pipeline_status` until `awaiting_approval`
5. Review the plan in Jira, then approve:
   `process_ticket(issue_key: "PIT-XX", approve_plan: true, force: true)`

### Mark stale tickets as obsolete

```
update_ticket_status(issue_key: "PIT-XX", status: "Obsolete")
```

---

## Known Issues & Gotchas

| Issue | Workaround |
|-------|------------|
| `auto_enrich: true` on `create_story` causes JSON parse errors | Always use `auto_enrich: false` |
| New tools not visible after server update | Disconnect and reconnect the connector in Claude.ai Settings, then start a fresh chat |
| `enrich_ticket` may be broken on some versions | Check `get_server_info` version; tracked as PIT-29 |
| Long-running pipeline calls may time out (older versions) | Fixed in v0.5.6+ with async pipeline; use `get_pipeline_status` to poll |

---

## Coding Standards (pitchvault-react)

The GigaCorp implementer is aware of these standards and should follow them automatically.
Reference them if reviewing a generated PR:

- **Language:** Plain JavaScript/JSX (migration to TypeScript in progress — PIT-50)
- **Components:** React 18 functional components with hooks only — no class components
- **Routing:** `@tanstack/react-router` — never `react-router-dom`
- **Linting:** ESLint 8 with `eslint-plugin-react`, `eslint-plugin-react-hooks`, `eslint-plugin-prettier` — lint errors fail CI
- **Formatting:** Prettier — semicolons on, single quotes, 2-space indent, trailing commas (es5), 100 char line width
- **Testing:** `@testing-library/react` + `@testing-library/jest-dom`; test files live alongside source as `src/Foo.test.js`
- **Test providers:** Always wrap components that use context hooks in the appropriate Provider in tests
- **CI/CD order:** `build` → `test --watchAll=false` → `lint` (all three must pass)

---

*Last updated: April 2026 — Server v0.5.6*
