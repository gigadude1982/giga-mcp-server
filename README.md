# giga-mcp-server

An MCP server that bridges WhatsApp group messages to JIRA Kanban board stories. Send an idea to a WhatsApp group, get a JIRA ticket created automatically.

## How it works

```
WhatsApp Group  ──>  giga-mcp-server  ──>  JIRA Kanban Board
     ^                                           |
     └───────── confirmation message ────────────┘
```

1. A human sends an idea or thought to a designated WhatsApp group
2. The server polls/reads new messages from the `whatsapp-mcp` SQLite store
3. Messages are parsed into structured JIRA fields (summary, priority, labels, issue type)
4. A story is created on the configured JIRA Kanban board
5. A confirmation with the ticket link is sent back to the WhatsApp group via the WhatsApp bridge

## Features

- **Dual parser**: Rule-based keyword extraction (default) or LLM-powered parsing via Claude Haiku
- **Deduplication**: Fuzzy-matches new ideas against recent issues to avoid duplicates
- **Retry logic**: Exponential backoff on JIRA API calls
- **MCP Inspector support**: `--inspect` mode with mock clients for development
- **File logging**: Optional log file output via `GIGA_LOG_FILE`
- **Cloud-ready**: Supports stdio and streamable-http transports

## MCP Tools

| Tool | Description |
|------|-------------|
| `process_message` | Manually create a JIRA story from text |
| `list_pending_ideas` | Query the intake/backlog column |
| `get_group_messages` | View recent WhatsApp group messages |
| `update_idea_status` | Transition a JIRA issue to a new status |
| `get_pipeline_status` | Health check (poll times, counts, errors) |

## Prerequisites

- Python 3.11+
- [whatsapp-mcp](https://github.com/lharries/whatsapp-mcp) Go bridge running locally
- Atlassian Cloud account with an [API token](https://id.atlassian.com/manage-profile/security/api-tokens)

## Setup

```bash
# Clone and install
git clone git@github.com:gigadude1982/giga-mcp-server.git
cd giga-mcp-server
python3.12 -m venv .venv
source .venv/bin/activate

# Development install
pip install -e ".[dev]"

# Development install with LLM parser support
# (use this instead of the command above if you want LLM features)
pip install -e ".[dev,llm]"
```

## Configuration

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

Key settings:

| Variable | Description |
|----------|-------------|
| `GIGA_WHATSAPP_GROUP_JID` | Target WhatsApp group JID (ends with `@g.us`) |
| `GIGA_JIRA_URL` | Atlassian instance URL |
| `GIGA_JIRA_USERNAME` | Atlassian account email |
| `GIGA_JIRA_API_TOKEN` | Atlassian API token |
| `GIGA_JIRA_PROJECT_KEY` | JIRA project key (e.g., `PROJ`) |
| `GIGA_PARSER_TYPE` | `rule_based` (default) or `llm` |
| `GIGA_ANTHROPIC_API_KEY` | Required if `GIGA_PARSER_TYPE=llm` |
| `GIGA_LOG_FILE` | Optional path for file logging |
| `GIGA_TRANSPORT` | `stdio` (default) or `streamable-http` |

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
        "GIGA_WHATSAPP_GROUP_JID": "your-group@g.us",
        "GIGA_JIRA_URL": "https://your-company.atlassian.net",
        "GIGA_JIRA_USERNAME": "you@company.com",
        "GIGA_JIRA_API_TOKEN": "your-token",
        "GIGA_JIRA_PROJECT_KEY": "PROJ"
      }
    }
  }
}
```

## Docker

```bash
docker compose up
```

The Docker setup builds the WhatsApp Go bridge and Python server into a single container. WhatsApp session data is persisted via a Docker volume.

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
├── models.py          # Data models (ParsedIdea, IdeaResult, WhatsAppMessage)
├── pipeline.py        # Orchestration: parse -> deduplicate -> create -> confirm
├── retry.py           # async_retry decorator with exponential backoff
├── inspect_stubs.py   # Mock clients for --inspect mode
├── whatsapp/
│   ├── client.py      # SQLite reader + HTTP sender
│   └── poller.py      # Background polling loop
├── jira/
│   └── client.py      # JIRA API wrapper (atlassian-python-api)
└── parser/
    ├── base.py        # Abstract parser interface
    ├── rule_based.py  # Keyword/regex parser
    └── llm_parser.py  # Claude Haiku parser
```

## License

Private repository.
