# giga-mcp-server *(codename: Bender)*

<img src="bender.png" align="left" width="200" alt="Bender robot illustration for giga-mcp-server" />

An MCP server that uses AI agents to enrich and autonomously implement JIRA tickets. Point it at a JIRA project and a GitHub repository and it will analyze tickets, enrich metadata, and — for tickets ready for implementation — plan, write, test, and open a pull request on GitHub entirely autonomously.

<br clear="left" />

## How it works

### Ticket enrichment (existing)

```
Human creates ticket  ──>  giga-mcp-server  ──>  Enriched JIRA ticket
                            (Claude AI)           - Priority & labels set
                                                  - Acceptance criteria added
                                                  - Subtasks created
                                                  - Duplicates flagged
```

### Autonomous implementation pipeline (new in v0.5.0)

```
process_ticket(PIT-42)
       │
       ▼
  [Digester]       Normalises ticket into structured spec
       │
       ▼
  [Planner]        Emits file list, approach, test strategy
       │
       ▼ ── posts plan to JIRA, pauses for human approval ──
       │
process_ticket(PIT-42, approve_plan=True)
       │
       ├──────────────────────────┐
       ▼                          ▼
[Implementer(s)]           [Test Writer(s)]   ← parallel
       │                          │
       └──────────┬───────────────┘
                  ▼
          [Validator]       Checks impl ↔ test coherence
                  │
                  ▼
          [PR Minter]       Writes PR title, body, commit message
                  │
                  ▼
       Atomic commit to branch → open PR → poll CI
                  │
                  ▼
       JIRA ticket → "In Review"
```

## Features

- **AI ticket creation**: Describe a feature or bug in plain English, get a structured JIRA story
- **AI enrichment**: Analyzes existing tickets and updates priority, labels, description, and acceptance criteria
- **Autonomous pipeline**: Full Digester → Planner → Implementer → Validator → PR Minter pipeline powered by Claude Sonnet (enrichment uses configurable Haiku by default)
- **Human-in-the-loop gate**: Pipeline pauses after the Planner, posts the plan to JIRA, and waits for explicit approval before writing any code
- **Atomic commits**: All file changes land in a single commit via the GitHub Git Data API — no intermediate states
- **CI integration**: Pipeline polls GitHub Actions after opening the PR and reports pass/fail back to JIRA
- **Batch processing**: Enrich all unprocessed backlog tickets in one call
- **Duplicate detection**: Fuzzy-matches tickets against recent issues to flag duplicates
- **Subtask generation**: Automatically splits large tickets into actionable subtasks
- **Retry logic**: Per-stage retry with exponential backoff; configurable `GIGA_PIPELINE_MAX_RETRIES`
- **OAuth support**: Optional Cognito JWT authentication for streamable-http transport
- **MCP Inspector support**: `--inspect` mode with mock clients for development
- **File logging**: Set `GIGA_LOG_FILE` to write structured logs to a file alongside stderr
- **Cloud-ready**: Supports stdio and streamable-http transports

## MCP Tools

### Enrichment tools

| Tool                   | Description                                                       |
| ---------------------- | ----------------------------------------------------------------- |
| `create_story`         | Create a JIRA ticket from a natural language description          |
| `analyze_ticket`       | AI-analyze a ticket and preview suggested enrichments (read-only) |
| `enrich_ticket`        | Analyze and apply AI enrichment to a JIRA ticket                  |
| `process_backlog`      | Batch-enrich unprocessed tickets in the backlog                   |
| `get_ticket`           | Fetch and display full details of a JIRA ticket                   |
| `list_backlog`         | List tickets filtered by status (pass `"All"` for every status)  |
| `update_ticket_status` | Transition a JIRA ticket to a new status                          |
| `find_duplicates`      | Check a ticket against recent issues for duplicates               |
| `get_server_info`      | Return server name, version, and runtime config                   |

### Autonomous pipeline tools

| Tool                  | Description                                                  |
| --------------------- | ------------------------------------------------------------ |
| `process_ticket`      | Run the autonomous implementation pipeline for a JIRA ticket |
| `get_pipeline_status` | Get the current status of a pipeline run                     |

#### `process_ticket` two-call flow

```
# Step 1 — digest + plan (pauses for review, posts plan to JIRA)
process_ticket(issue_key="PIT-42")

# Step 2 — approve plan, implement, test, open PR
process_ticket(issue_key="PIT-42", approve_plan=True)

# Force reprocessing of an already-implemented ticket
process_ticket(issue_key="PIT-42", force=True)
```

## Prerequisites

- Python 3.11+
- Atlassian Cloud account with an [API token](https://id.atlassian.com/manage-profile/security/api-tokens)
- Anthropic API key for Claude
- GitHub account with a [personal access token](https://github.com/settings/tokens) — classic token with `repo` and `workflow` scopes (for the autonomous pipeline)

## Setup

```bash
# Clone and install
git clone git@github.com:gigadude1982/giga-mcp-server.git
cd giga-mcp-server
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Configuration

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

### Required settings

| Variable                 | Description                    |
| ------------------------ | ------------------------------ |
| `GIGA_JIRA_URL`          | Atlassian instance URL         |
| `GIGA_JIRA_USERNAME`     | Atlassian account email        |
| `GIGA_JIRA_API_TOKEN`    | Atlassian API token            |
| `GIGA_JIRA_PROJECT_KEY`  | JIRA project key (e.g., `PIT`) |
| `GIGA_ANTHROPIC_API_KEY` | Anthropic API key for Claude   |

### Pipeline settings (required for `process_ticket`)

| Variable                          | Default                                      | Description                                                                                |
| --------------------------------- | -------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `GIGA_GITHUB_TOKEN`               | —                                            | GitHub [classic PAT](https://github.com/settings/tokens) with `repo` and `workflow` scopes |
| `GIGA_GITHUB_REPO`                | —                                            | Target repo in `owner/repo` format                                                         |
| `GIGA_GITHUB_BASE_BRANCH`         | `main`                                       | Branch to create feature branches from                                                     |
| `GIGA_PIPELINE_HUMAN_GATE`        | `true`                                       | Pause after Planner for human approval                                                     |
| `GIGA_PIPELINE_MAX_RETRIES`       | `3`                                          | Per-stage retry limit                                                                      |
| `GIGA_PIPELINE_COMMIT_AUTHOR_NAME`  | `giga-pipeline[bot]`                       | Display name on pipeline commits                                                           |
| `GIGA_PIPELINE_COMMIT_AUTHOR_EMAIL` | `giga-pipeline[bot]@users.noreply.github.com` | Email on pipeline commits                                                              |

### Optional settings

| Variable                    | Default      | Description                                             |
| --------------------------- | ------------ | ------------------------------------------------------- |
| `GIGA_TRANSPORT`            | `stdio`      | `stdio` or `streamable-http`                            |
| `GIGA_HOST`                 | `0.0.0.0`    | Bind host (streamable-http only)                        |
| `GIGA_PORT`                 | `8000`       | Bind port (streamable-http only)                        |
| `GIGA_LOG_FILE`             | —            | Path for file logging (logs to file + stderr)           |
| `GIGA_INSPECT`              | `false`      | Use mock clients for MCP Inspector / development        |
| `GIGA_COGNITO_USER_POOL_ID` | —            | Enables OAuth (Cognito JWT verification) when set       |
| `GIGA_COGNITO_REGION`       | `us-east-1`  | Cognito region                                          |
| `GIGA_COGNITO_CLIENT_ID`    | —            | Restrict to a specific Cognito app client               |
| `GIGA_PUBLIC_URL`           | —            | Public URL for OAuth resource metadata                  |
| `GIGA_JIRA_DEFAULT_ISSUE_TYPE` | `Story`   | Default issue type when creating tickets                |
| `GIGA_JIRA_DEFAULT_PRIORITY`   | `Medium`  | Default priority when creating tickets                  |
| `GIGA_JIRA_INTAKE_STATUS`      | `To Do`   | Status assigned to newly created tickets                |
| `GIGA_JIRA_PROCESSED_LABEL`    | `ai-processed` | Label added to enriched tickets                    |
| `GIGA_ANTHROPIC_MODEL`         | `claude-haiku-4-5-20251001` | Claude model for enrichment; pipeline always uses Sonnet |

### Repo pipeline config (optional)

Add a `.giga-pipeline.json` to the root of any target repo to override defaults:

```json
{
  "language": "python",
  "test_framework": "pytest",
  "test_command": "pytest",
  "coding_standards": "Follow PEP 8. Use type hints. Use structlog for logging.",
  "source_dirs": ["src"],
  "test_dirs": ["tests"],
  "max_retries_per_stage": 3,
  "human_gate_after_planner": true,
  "branch_prefix": "auto/"
}
```

If the file is absent, sensible defaults are used.

## Usage

See the [usage guide](giga-mcp-server-usage-guide.md) for Claude/MCP server integration setup and tool reference.

### Run with MCP Inspector (no credentials needed)

```bash
scripts/inspect-local.sh
```

### Run in production (stdio)

```bash
giga-mcp-server
```

### Run with streamable HTTP transport

```bash
GIGA_TRANSPORT=streamable-http giga-mcp-server
```

### Claude Desktop configuration

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "giga-mcp-server": {
      "command": "/path/to/giga-mcp-server/.venv/bin/giga-mcp-server",
      "env": {
        "GIGA_JIRA_URL": "https://your-company.atlassian.net",
        "GIGA_JIRA_USERNAME": "you@company.com",
        "GIGA_JIRA_API_TOKEN": "your-token",
        "GIGA_JIRA_PROJECT_KEY": "PIT",
        "GIGA_ANTHROPIC_API_KEY": "sk-ant-...",
        "GIGA_GITHUB_TOKEN": "ghp_...",
        "GIGA_GITHUB_REPO": "owner/repo"
      }
    }
  }
}
```

## Docker

```bash
docker compose up
```

## Deployment

The server deploys to AWS App Runner via GitHub Actions. Pushing to `main` triggers:

1. Lint + test (`ruff check` + `pytest`)
2. Docker image build and push to ECR
3. App Runner service update

Manual deploy: `scripts/deploy.sh` (first deploy) or `scripts/deploy.sh --update` (redeploy).

## Scripts

| Script                      | Description                                                      |
| --------------------------- | ---------------------------------------------------------------- |
| `scripts/inspect-local.sh`  | Launch MCP Inspector with local mock server                      |
| `scripts/inspect-remote.sh` | Launch MCP Inspector for remote server                           |
| `scripts/deploy.sh`         | Deploy to App Runner via ECR                                     |
| `scripts/setup-auth.sh`     | Set up Cognito auth (create pool, client, test user, get tokens) |

## Development

```bash
# Run tests
pytest tests/ -v

# Lint
ruff check src/ tests/

# Run tests + lint (run before every commit)
pytest tests/ -v && ruff check src/ tests/

# After bumping the version in pyproject.toml, reinstall so the running server picks it up
pip install -e .
```

## Architecture

```
src/giga_mcp_server/
├── server.py          # FastMCP server, tool definitions, lifespan
├── config.py          # Pydantic settings (env vars)
├── models.py          # Data models (ParsedIdea, TicketAnalysis, EnrichmentResult)
├── enrichment.py      # AI ticket analysis & enrichment using Claude
├── auth.py            # Cognito JWT token verifier for OAuth
├── retry.py           # async_retry decorator with exponential backoff
├── inspect_stubs.py   # Mock clients for --inspect mode
├── jira/
│   └── client.py      # JIRA API wrapper (atlassian-python-api)
└── pipeline/
    ├── agent_prompts.py   # 6 agent contracts (system prompts + I/O JSON schemas)
    ├── agent_runner.py    # Claude Sonnet calls with schema validation + retry
    ├── github_tools.py    # GitHub Data API: branches, files, atomic commits, PRs, CI polling
    ├── jira_bridge.py     # ADF text extraction + pipeline-facing JIRA wrappers
    ├── orchestrator.py    # Full pipeline: Digester→Planner→Impl∥Test→Validator→PRMinter
    └── repo_config.py     # .giga-pipeline.json loader with defaults
```

## License

MIT © 2026 Dalton B. Mangrum — see [LICENSE](LICENSE).
