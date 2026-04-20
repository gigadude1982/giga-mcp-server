# giga-mcp-server

An MCP server that uses AI agents to enrich and process JIRA tickets. Point it at a JIRA project and it will analyze tickets, suggest priorities and labels, generate acceptance criteria, detect duplicates, and break epics into subtasks — all powered by Claude.

## How it works

```
Human creates ticket  ──>  giga-mcp-server  ──>  Enriched JIRA ticket
                            (Claude AI)           - Priority & labels set
                                                  - Acceptance criteria added
                                                  - Subtasks created
                                                  - Duplicates flagged
```

1. A human creates a rough JIRA ticket (or describes one in natural language)
2. The server analyzes the ticket using Claude Haiku
3. AI enriches the ticket: sets priority, labels, issue type, and writes acceptance criteria
4. Large tickets are split into subtasks automatically
5. Duplicate detection flags similar existing issues

## Features

- **AI ticket creation**: Describe a feature or bug in plain English, get a structured JIRA story
- **AI enrichment**: Analyzes existing tickets and updates priority, labels, description, and acceptance criteria
- **Batch processing**: Enrich all unprocessed backlog tickets in one call
- **Duplicate detection**: Fuzzy-matches tickets against recent issues to flag duplicates
- **Subtask generation**: Automatically splits large tickets into actionable subtasks
- **Retry logic**: Exponential backoff on JIRA API calls
- **OAuth support**: Optional Cognito JWT authentication for streamable-http transport
- **MCP Inspector support**: `--inspect` mode with mock clients for development
- **File logging**: Optional log file output via `GIGA_LOG_FILE`
- **Cloud-ready**: Supports stdio and streamable-http transports

## MCP Tools

| Tool | Description |
|------|-------------|
| `create_story` | Create a JIRA ticket from a natural language description |
| `analyze_ticket` | AI-analyze a ticket and preview suggested enrichments (read-only) |
| `enrich_ticket` | Analyze and apply AI enrichment to a JIRA ticket |
| `process_backlog` | Batch-enrich unprocessed tickets in the backlog |
| `get_ticket` | Fetch and display full details of a JIRA ticket |
| `list_backlog` | List tickets in the project backlog |
| `update_ticket_status` | Transition a JIRA ticket to a new status |
| `find_duplicates` | Check a ticket against recent issues for duplicates |

## Prerequisites

- Python 3.11+
- Atlassian Cloud account with an [API token](https://id.atlassian.com/manage-profile/security/api-tokens)
- Anthropic API key for Claude

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

Key settings:

| Variable | Description |
|----------|-------------|
| `GIGA_JIRA_URL` | Atlassian instance URL |
| `GIGA_JIRA_USERNAME` | Atlassian account email |
| `GIGA_JIRA_API_TOKEN` | Atlassian API token |
| `GIGA_JIRA_PROJECT_KEY` | JIRA project key (e.g., `PIT`) |
| `GIGA_ANTHROPIC_API_KEY` | Anthropic API key for Claude |
| `GIGA_TRANSPORT` | `stdio` (default) or `streamable-http` |
| `GIGA_LOG_FILE` | Optional path for file logging |
| `GIGA_COGNITO_USER_POOL_ID` | Optional: enables OAuth (Cognito JWT verification) |
| `GIGA_COGNITO_REGION` | Cognito region (default: `us-east-1`) |
| `GIGA_COGNITO_CLIENT_ID` | Optional: restrict to a specific Cognito app client |
| `GIGA_PUBLIC_URL` | Public URL for OAuth resource metadata |

See [.env.example](.env.example) for all options.

## Usage

### Run with MCP Inspector (no credentials needed)

```bash
npx @modelcontextprotocol/inspector -- .venv/bin/python -m giga_mcp_server.server --inspect
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
        "GIGA_ANTHROPIC_API_KEY": "sk-ant-..."
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

## Development

```bash
# Run tests
pytest tests/ -v

# Lint
ruff check src/ tests/

# Run tests + lint
pytest tests/ -v && ruff check src/ tests/
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
└── jira/
    └── client.py      # JIRA API wrapper (atlassian-python-api)
```

## License

Private repository.
