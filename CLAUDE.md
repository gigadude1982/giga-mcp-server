# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Common commands

```bash
# Run tests + lint (the pre-commit gate)
pytest tests/ -v && ruff check src/ tests/

# Run a single test
pytest tests/test_enrichment.py::test_name -v

# Run the server locally over stdio
giga-mcp-server

# Launch MCP Inspector against a local mock server (no creds needed)
scripts/inspect-local.sh

# Launch MCP Inspector against a deployed board
scripts/inspect-remote.sh                  # gigacorp (default)
scripts/inspect-remote.sh --board pitchvault

# After bumping version in pyproject.toml, reinstall so the entrypoint picks it up
pip install -e .
```

The CLI entrypoint `giga-mcp-server` is wired in `pyproject.toml:33` to `server:main`. CI runs `ruff check` + `pytest` on push to `main`, then builds a Docker image and pushes to ECR — App Runner services auto-redeploy from `:latest`.

## Architecture big picture

This is a **multi-tenant MCP server**: one codebase, one Docker image, one App Runner service per JIRA-board ↔ GitHub-repo pair. Each board lives as one entry in `infra/config/boards.ts` and gets its own SSM secrets, Cognito user pool, and subdomain. Adding a board is a one-line change + `cdk deploy`.

There are two distinct subsystems sharing the JIRA client:

**1. Enrichment** (`src/giga_mcp_server/enrichment.py`) — single-shot Claude calls that analyze a ticket and update fields/labels/subtasks. Uses the cheaper `GIGA_ANTHROPIC_MODEL` (Haiku by default).

**2. Autonomous implementation pipeline** (`src/giga_mcp_server/pipeline/`) — multi-stage agent pipeline that writes code and opens PRs. Always uses Sonnet (overridable via `.giga-pipeline.json`'s `pipeline_model`). The stages are defined as prompts + I/O JSON schemas in `pipeline/agent_prompts.py:AGENT_REGISTRY`:

```
Digester → Planner → [Implementers ∥ Test Writers] → Validator ↺ → PR Minter
                          ↑________retry on validator fail________|
```

The validator → implementer feedback loop runs up to `GIGA_PIPELINE_MAX_RETRIES` times. Files land via the GitHub Git Data API as a single atomic commit (`pipeline/github_tools.py`) — no intermediate states.

### Two-call `process_ticket` flow

`process_ticket` is the only tool with non-trivial state. It runs the pipeline as a background `asyncio.Task` and is gated by `GIGA_PIPELINE_HUMAN_GATE`:

- Call 1: `process_ticket(issue_key="PIT-42")` → runs Digester + Planner, posts plan to JIRA, status becomes `awaiting_approval`.
- Call 2: `process_ticket(issue_key="PIT-42", approve_plan=True)` → resumes from the saved plan, runs Implementer/Test Writer/Validator/PR Minter.
- `force=True` reprocesses tickets in terminal JIRA statuses; `force=True, approve_plan=True` together skips the human gate end-to-end.

Pipeline state lives in `AppContext.pipeline_runs: dict[str, PipelineState]` (`server.py:48`). **It is in-memory only — restarting the server loses all in-flight runs.** Anything depending on persistence across restarts needs to read JIRA status, not the in-memory dict.

### Lifespan modes

`server.py:lifespan` switches on `GIGA_INSPECT`:
- **inspect mode** (`--inspect` or `GIGA_INSPECT=true`): uses `inspect_stubs.MockJiraClient`/`MockTicketEnricher`. No credentials required. Use this whenever testing tool wiring without hitting JIRA.
- **production mode**: requires the env vars listed in `Settings.validate_required()` (`config.py:61`).

### Per-repo pipeline config

`.giga-pipeline.json` at the root of any **target** repo (the repo the pipeline writes to, not this one) overrides defaults from `pipeline/repo_config.py:_DEFAULTS`. The pipeline also auto-fetches `.prettierrc`, `.eslintrc`, and `.editorconfig` from the target repo and concatenates them into `coding_standards` so the implementer has the formatting rules verbatim. There is no formatter step — generated code is committed directly, so prompt-level formatting compliance is load-bearing.

## Things that bite

- **The agent prompts in `pipeline/agent_prompts.py` are React-web-specific.** Implementer/test_writer/validator rules cover PropTypes, CSS modules, JSX/Prettier formatting, the React 17+ automatic JSX transform, etc. Pointing the pipeline at a non-React stack will produce noise or actively wrong code until the prompts are refactored into language/framework-aware rule packs (the `language` field in `repo_config.py` is the natural seam).
- **Bumping `pyproject.toml` version requires `pip install -e .`** before the new version shows in `get_server_info`.
- **Don't let the pipeline plan changes to CI/CD workflows or deploy config** — the planner prompt explicitly forbids this; preserve that rule when editing prompts.
- **Pinecone vector store is opt-in per board** via `vectorEnabled` in `boards.ts`. When disabled, `VectorStore` is `None` and enrichment falls back to the fuzzy-match duplicate detector. After enabling on a board, run the `backfill_vectors` MCP tool once to seed history.
- **Missing JIRA workflow statuses** (`In Plan Review`, `In Development`, `In Code Review`) are auto-created by the pipeline but not auto-wired into the workflow — JIRA admin still has to add the transitions, and the pipeline logs a hint when this is needed.

## Deployment

Infra is AWS CDK in TypeScript under `infra/`. The single stack in `infra/lib/giga-mcp-server-stack.ts` provisions one ECR repo plus one `giga-mcp-server-service` construct per board. Push to `main` → GitHub Actions builds the image and pushes `:latest` → all App Runner services auto-redeploy. CDK deploy is only needed when adding/changing a board, not for code changes.
