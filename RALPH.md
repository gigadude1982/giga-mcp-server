# Ralph loop

Autonomous Claude Code loop that works through `RALPH-BACKLOG.md`, opening one PR per item.

## Run

```bash
./scripts/ralph-loop.sh
```

## Stop

- `touch .ralph-stop` — graceful, exits after the current iteration
- `Ctrl-C` — immediate
- Empty backlog — the agent creates `.ralph-stop` itself and the loop exits next tick

## Before first run

1. Edit `RALPH-BACKLOG.md` so the top items are well-scoped (≤1 PR of work each).
2. Turn on **branch protection** for `main` in GitHub (settings → branches). The loop forbids the agent from pushing `main`, but branch protection is the hard guarantee.
3. Make sure `claude` and `gh` are on PATH and `gh auth status` is green.
4. Commit any in-progress local changes — the loop aborts on a dirty working tree.

## Tunables

- `RALPH_MAX_ITERS` (default 50) — hard cap on iterations
- `RALPH_SLEEP` (default 30) — seconds between iterations

## What it can't touch

The prompt forbids the agent from editing:
- `infra/` (CDK)
- `.github/workflows/` (CI)
- `pyproject.toml` version field (release-coupled)
- `scripts/ralph-loop.sh` (itself)

## Logs

Everything goes to `ralph.log` (gitignored). Tail it with `tail -f ralph.log`.
