#!/usr/bin/env bash
# Ralph loop — runs `claude -p` in a loop against RALPH-BACKLOG.md until the
# backlog is empty or `.ralph-stop` appears.
#
# Usage:
#   ./scripts/ralph-loop.sh
#
# Stop:
#   touch .ralph-stop          # graceful, after current iteration finishes
#   Ctrl-C                     # immediate
#
# Safety model:
#   - Each iteration starts from a clean `main` synced with origin.
#   - The agent is mandated to work on a `ralph/<slug>` branch and open a PR.
#   - Uncommitted changes in the working tree abort the loop (we don't want to
#     stomp on your in-progress work).
#   - Forbidden paths are listed in the prompt; rely on GitHub branch protection
#     on `main` for the hard guarantee — turn that on before running this.
#   - Iterations are logged to `ralph.log` with timestamps for audit.
#
# Cost note: each iteration is a fresh `claude -p` context (no cache). Keep the
# backlog file small and focused; bloated context = bloated bill.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

BACKLOG_FILE="RALPH-BACKLOG.md"
STOP_FILE=".ralph-stop"
LOG_FILE="ralph.log"
MAX_ITERS="${RALPH_MAX_ITERS:-50}"
SLEEP_BETWEEN="${RALPH_SLEEP:-30}"

if [[ ! -f "$BACKLOG_FILE" ]]; then
    echo "ERROR: $BACKLOG_FILE not found in $REPO_DIR" >&2
    exit 1
fi

if ! command -v claude >/dev/null 2>&1; then
    echo "ERROR: claude CLI not on PATH" >&2
    exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
    echo "ERROR: gh CLI not on PATH (agent needs it to open PRs)" >&2
    exit 1
fi

# Clear any stale stop file from a previous run
rm -f "$STOP_FILE"

log() {
    printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOG_FILE"
}

PROMPT='You are running inside an autonomous Ralph loop in the giga-mcp-server repo.

YOUR JOB: read RALPH-BACKLOG.md, pick the topmost unchecked item, implement it, open a PR.

RULES (hard):
- Branch off main as `ralph/<short-slug>`. NEVER commit to main. NEVER push main.
- Forbidden paths — do not modify any of:
    infra/                       (CDK / deploy config)
    .github/workflows/           (CI)
    pyproject.toml version field (release-coupled)
    scripts/ralph-loop.sh        (you, recursively)
    RALPH-BACKLOG.md history of completed items (only check off your current item)
- Run the pre-commit gate before pushing: `pytest tests/ -v && ruff check src/ tests/`.
  If it fails, fix until it passes. Do not commit failing code.
- Open exactly one PR per iteration with `gh pr create`. Leave it for human review.
- In the same commit that contains your code change, edit RALPH-BACKLOG.md to mark
  your item as `- [x]` (done) and leave a one-line note linking to the PR.
- If RALPH-BACKLOG.md has no unchecked items, `touch .ralph-stop` and exit cleanly.
- If you get blocked (ambiguous spec, missing access, etc.), add a `BLOCKED:` note
  to the backlog item explaining why, leave it unchecked, and exit. The next
  iteration will skip it (treat any item with a `BLOCKED:` note as already-handled).

START NOW.'

iter=0
while [[ $iter -lt $MAX_ITERS ]]; do
    iter=$((iter + 1))

    if [[ -f "$STOP_FILE" ]]; then
        log "stop file present; exiting after $((iter - 1)) iterations"
        exit 0
    fi

    # Refuse to run on a dirty tree — too easy to lose work
    if [[ -n "$(git status --porcelain)" ]]; then
        log "ERROR: working tree dirty, aborting. Commit/stash first."
        exit 1
    fi

    log "iter $iter: syncing main"
    git switch main >/dev/null 2>&1
    git pull --ff-only origin main

    log "iter $iter: invoking claude"
    if ! claude -p --dangerously-skip-permissions "$PROMPT" 2>&1 | tee -a "$LOG_FILE"; then
        log "ERROR: claude exited non-zero; stopping loop"
        exit 1
    fi

    # Return to main so the next iteration starts clean even if agent left us on a branch
    git switch main >/dev/null 2>&1 || true

    log "iter $iter: done, sleeping ${SLEEP_BETWEEN}s"
    sleep "$SLEEP_BETWEEN"
done

log "hit max iterations ($MAX_ITERS); exiting"
