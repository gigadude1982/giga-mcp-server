# Ralph backlog

Items the Ralph loop will work through, top to bottom. One item = one PR.

Format:
- `- [ ] <title>` — TODO, agent will pick the topmost of these
- `- [x] <title>` — DONE (agent leaves a PR link as a sub-bullet)
- Any item with a `BLOCKED:` sub-bullet is skipped

Keep items small and self-contained (≤1 PR of work). Vague items produce vague PRs.

## Backlog

- [ ] Add `--version` flag to the `giga-mcp-server` CLI that prints the version from `pyproject.toml` and exits 0
  - Reads version from `importlib.metadata.version("giga-mcp-server")`
  - Wire it in `server.py:main` before the MCP server starts
  - Add a unit test in `tests/`

- [ ] Add structured logging field `board_id` to every log line emitted from `server.py` and `enrichment.py`
  - Use `logging.LoggerAdapter` so the field is automatic, not added per-call
  - Update tests to assert the field is present

- [ ] Document the `GIGA_PIPELINE_MAX_RETRIES` env var in `CLAUDE.md` under "Things that bite"
  - Note the default value and which file reads it
