# Plane Support — design spike

Spike doc for making the issue tracker pluggable so a board can use either Atlassian JIRA Cloud or [Plane](https://plane.so) (free, self-hostable / cloud OSS alternative). Not committed to a timeline. Delete this file once the work ships or the idea is dropped.

Started 2026-05-28.

## Goal

A board entry in `infra/config/boards.ts` should be able to say:

```ts
{
  boardId: "punch-pwa",
  trackerType: "plane",                    // ← new field
  trackerUrl: "https://plane.gigacorp.co", // or app.plane.so
  trackerWorkspace: "gigacorp",
  trackerProjectId: "<plane-project-uuid>",
  ...
}
```

…and everything downstream — `process_ticket`, enrichment, `create_ticket`, the pipeline's plan/PR comments — Just Works against Plane instead of JIRA. The choice is **per board**, so PUNCH could be Plane while gigacorp + pitchvault stay on JIRA.

## Current JIRA surface (what the protocol has to cover)

`src/giga_mcp_server/jira/client.py:JiraClient` is the only place that talks to Atlassian. Public methods used elsewhere in the codebase:

| Method                                | Used by                                  | Notes                                                |
| ------------------------------------- | ---------------------------------------- | ---------------------------------------------------- |
| `get_issue(key)`                      | enrichment, pipeline digester, server    | returns the normalized dict — already shape-agnostic |
| `update_issue(key, fields)`           | enrichment                               | fields shape is JIRA-flavored today                  |
| `add_comment(key, body)`              | enrichment, pipeline, PR minter          | plain-text body, easy to map                         |
| `get_comments(key, max)`              | enrichment                               | filters pipeline-generated comments by prefix        |
| `transition_issue(key, status)`       | pipeline (Plan Review / In Dev / etc.)   | hardest to map cleanly — workflows differ            |
| `create_ticket(idea)`                 | server `create_ticket` tool              | issue-type resolution is JIRA-specific               |
| `create_subtask(parent, summary, …)`  | enrichment                               | Plane has sub-issues but the model is different      |
| `search_issues(jql, max)`             | enrichment dedupe, calibration           | JQL → Plane filter syntax conversion needed          |
| `search_issues_full(jql, max)`        | enrichment dedupe, calibration           | same as above + ADF→text                             |
| `search_ticket_examples(jql, …)`      | enrichment calibration                   | thin wrapper over `search_issues_full`               |
| `get_project_issue_types()`           | `create_ticket` issue-type resolution    | Plane has its own types model                        |
| `_ensure_status_exists(name)`         | `transition_issue` recovery path         | Plane states are project-scoped — different model    |

Files that import `JiraClient` or `jira_client`:
- `src/giga_mcp_server/enrichment.py`
- `src/giga_mcp_server/server.py` (constructs it in `lifespan`)
- `src/giga_mcp_server/inspect_stubs.py` (`MockJiraClient`)
- `src/giga_mcp_server/pipeline/jira_bridge.py` (wraps it for the pipeline)
- `src/giga_mcp_server/pipeline/orchestrator.py` (consumes via bridge)

`pipeline/agent_prompts.py` references "JIRA" in 5 places (digester role, PR minter role + comment field, ticket-key extraction). These need de-JIRA-ing.

## Proposed abstraction

### Step 1 — extract `IssueTrackerClient` Protocol

```python
# src/giga_mcp_server/trackers/protocol.py
from typing import Protocol, Any
from giga_mcp_server.models import ParsedIdea, IdeaResult

class IssueTrackerClient(Protocol):
    async def get_issue(self, key: str) -> dict[str, Any]: ...
    async def update_issue(self, key: str, fields: dict[str, Any]) -> bool: ...
    async def add_comment(self, key: str, body: str) -> bool: ...
    async def get_comments(self, key: str, max_comments: int = 10) -> list[str]: ...
    async def transition_issue(self, key: str, status: str) -> bool: ...
    async def create_ticket(self, idea: ParsedIdea) -> IdeaResult: ...
    async def create_subtask(self, parent: str, summary: str, description: str = "") -> IdeaResult: ...
    async def search_issues(self, query: str, max_results: int = 20) -> list[dict[str, Any]]: ...
    async def search_issues_full(self, query: str, max_results: int = 20) -> list[dict[str, Any]]: ...
    async def search_ticket_examples(self, query: str, limit: int = 5, desc_limit: int = 500) -> list[dict[str, Any]]: ...
    async def get_project_issue_types(self) -> list[str]: ...
```

Move `JiraClient` to `src/giga_mcp_server/trackers/jira.py`. Keep a re-export at the old path for one release if anything imports it directly outside the package.

Normalize the **return** shape — `get_issue` already returns a tracker-agnostic dict; make sure every method does the same. The Plane impl converts Plane's `state`/`module`/`cycle` model into the same dict shape.

Normalize the **input** shape for `update_issue` — today callers pass JIRA-flavored field names (`{"labels": [...], "priority": {"name": "High"}}`). Define a `IssueUpdate` dataclass with neutral field names and translate inside each client.

### Step 2 — implement `PlaneTrackerClient`

```python
# src/giga_mcp_server/trackers/plane.py
class PlaneTrackerClient:
    def __init__(self, settings: Settings) -> None:
        self._base = settings.tracker_url.rstrip("/")
        self._workspace = settings.tracker_workspace
        self._project = settings.tracker_project_id
        self._token = settings.tracker_api_token
        # httpx.AsyncClient with bearer auth
```

Plane's REST API ([docs](https://docs.plane.so/api-reference)) is well-shaped — issues live at `/api/v1/workspaces/{slug}/projects/{id}/issues/`. Filter syntax is query-param-based, not JQL. Comments at `…/issues/{id}/comments/`. States are per-project at `…/states/`. Sub-issues at `…/issues/{parent}/sub-issues/`.

**JQL → Plane filter translation:** keep it dumb. Today the callsites build JQL like `project = PIT AND status != Done`. Plane wants `?state__name__in=Backlog,Todo`. Introduce a tiny `TrackerQuery` builder used by both impls instead of passing raw JQL strings around. Lets the Plane client translate cleanly.

### Step 3 — factory + config

In `config.py`:

```python
class Settings:
    tracker_type: Literal["jira", "plane"] = "jira"
    tracker_url: str          # was jira_url
    tracker_workspace: str    # plane-only; empty for jira
    tracker_project_key: str  # was jira_project_key (PUNCH for jira, project UUID for plane)
    tracker_username: str     # was jira_username; email for both
    tracker_api_token: str    # was jira_api_token
    # …
```

Keep `jira_*` aliases for one release so SSM params don't need to be renamed in a single deploy. Drop them next quarter.

In `server.py:lifespan`:

```python
from giga_mcp_server.trackers import build_tracker_client
tracker = build_tracker_client(settings)  # picks Jira vs Plane by settings.tracker_type
```

In `infra/config/boards.ts`:

```ts
interface BoardConfig {
  trackerType?: "jira" | "plane";   // defaults to "jira"
  // existing fields renamed: jiraUrl → trackerUrl, jiraProjectKey → trackerProjectKey, …
}
```

CDK stack passes `TRACKER_TYPE`, `TRACKER_URL`, `TRACKER_WORKSPACE`, `TRACKER_PROJECT_KEY` to the App Runner env.

### Step 4 — de-JIRA the prompts

`pipeline/agent_prompts.py` lines 8, 12, 450, 454, 526 hard-code "JIRA". Replace with:

- "JIRA ticket" → "the issue tracker ticket"  *(or)*  pass tracker name as a template variable so the prompt reads naturally
- Ticket-key extraction regex needs both `PIT-123` (JIRA) and Plane's `WORKSPACE-123` shape. Plane sequence IDs are scoped per-project and look identical to JIRA's, so the regex probably doesn't need changing — verify.

### Step 5 — webhook parity

`.github/workflows/jira-done-on-merge.yml` calls JIRA's REST API to transition tickets to Done when a PR merges. Either:

- (a) generalize it: workflow detects tracker type from PR labels / branch prefix and dispatches, **or**
- (b) ship a sibling `plane-done-on-merge.yml`. Cheaper, fine for now.

### Step 6 — inspect_stubs

`MockJiraClient` in `inspect_stubs.py` already returns the neutral dict shape. Rename to `MockTrackerClient`, add a `mock_tracker_type` switch only if you want to surface Plane-specific quirks in inspect mode. Probably not needed in v1.

## Catches / known unknowns

- **Plane workflow states are project-scoped and finite.** Default Plane projects ship with `Backlog`, `Todo`, `In Progress`, `Done`, `Cancelled`. The pipeline today wants to create `In Plan Review`, `In Development`, `In Code Review` on-demand. Plane has an API to create states (`POST …/states/`), so this is feasible, but the JIRA fallback ("admin must wire status into workflow") doesn't apply — Plane states are immediately usable once created. Net: easier than JIRA, but the code path diverges.
- **No JQL.** Anything that builds a JQL string at the callsite (search-based dedupe, calibration) needs to move through the `TrackerQuery` builder. Estimate: ~5 callsites, all in `enrichment.py`.
- **ADF descriptions don't exist in Plane.** Plane uses HTML/markdown. `_adf_to_text` is JIRA-only; the Plane client returns markdown directly. Pipeline prompts already expect markdown-ish input, so this is actually simpler.
- **Sub-issue / parent linking.** Plane sub-issues have a `parent` field on the issue itself, not a separate "Sub-task" issue type. The `create_subtask` adapter for Plane is `PATCH …/issues/{id}/` with `{parent: parent_id}` after creating the issue.
- **`jira-done-on-merge` secret name fallback** (see commit `c97d88a`) is JIRA-shaped. Plane uses a single API token, simpler.
- **Vector store doesn't care.** Pinecone embeddings are pure text → tracker-agnostic. No work needed there.
- **GitHub side doesn't care.** PR creation, branch management, Git Data API all unaffected.

## Out of scope for v1

- Plane self-hosting on AWS. Use Plane Cloud (free tier) for the demo board.
- Two-way sync between JIRA and Plane on the same board. Each board picks one.
- Real-time webhooks from Plane back to Bender (e.g. for human comments). Today JIRA→Bender is comment-driven via `process_ticket` polling, not webhooks. Same pattern works for Plane.
- Custom field parity. JIRA's custom fields are deeply weird; Plane has a flatter model. v1 supports the standard fields only.

## Phasing

| Phase                                     | Effort  | Risk                                                  |
| ----------------------------------------- | ------- | ----------------------------------------------------- |
| 1. Extract `IssueTrackerClient` protocol  | ~1 day  | Low — no behavior change, all tests should still pass |
| 2. Implement `PlaneTrackerClient`         | ~1 day  | Medium — Plane API quirks discovered as you go       |
| 3. Factory + config + SSM key rename      | ~0.5 day| Low — keep aliases for one release                    |
| 4. De-JIRA prompts                        | ~0.5 day| Low — token replacement + one regex check             |
| 5. `plane-done-on-merge.yml`              | ~0.5 day| Low                                                   |
| 6. Integration test against Plane Cloud   | ~0.5 day| Medium — first real Plane round-trip                  |

**Total: 3-4 days** for a working spike that can run a board end-to-end on Plane.

## Demo angle (interview pitch)

If asked about extensibility, the honest answer is: "The board ↔ subdomain ↔ secrets ↔ App Runner axis is already pluggable. The tracker axis isn't — it's load-bearing on Atlassian JIRA. Here's the spike doc for the JIRA→Plane abstraction; about 3-4 days to ship." Strong signal that you've thought about the next layer of the design without over-building it.
