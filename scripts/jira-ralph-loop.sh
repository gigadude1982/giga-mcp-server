#!/usr/bin/env bash
# jira-ralph-loop.sh — autonomous JIRA-driven loop for any giga-mcp-server board.
#
# Picks up tickets labeled with a trigger label (default: `auto-ready`) in a
# board's JIRA project and drives them through process_ticket(force=true,
# approve_plan=true) end-to-end. Sleeps, repeats. Designed to run unattended on
# a laptop overnight / over a weekend.
#
# Usage:
#   ./scripts/jira-ralph-loop.sh --board gigacorp-react
#   ./scripts/jira-ralph-loop.sh --board pitchvault-react --label custom
#   ./scripts/jira-ralph-loop.sh --board gigacorp-react --mcp-url https://...
#
# Stop:
#   touch .jira-ralph-<board>.stop   # graceful, exits after current ticket finishes
#   Ctrl-C                            # immediate (in-flight pipeline keeps running server-side)
#
# Per iteration the agent:
#   1. Lists candidate tickets in "To Do" status that aren't already ai-processed.
#   2. Inspects each candidate's labels client-side; picks the first one carrying $LABEL.
#   3. Calls process_ticket(force=true, approve_plan=true) — full pipeline, no human gate.
#   4. Polls get_pipeline_status until terminal (complete / failed / error / timeout).
#   5. Strips $LABEL on success; on failure also adds `auto-failed` for human triage.
#
# Stops when:
#   - No tickets carry $LABEL (agent writes the stop file itself)
#   - You touch .jira-ralph-<board>.stop
#   - Max iterations reached
#
# Tunables (env):
#   JIRA_RALPH_MAX_ITERS  (default 50)   — hard cap on iterations
#   JIRA_RALPH_SLEEP      (default 60)   — seconds between iterations
#
# Cost note: each iteration spins up a fresh `claude -p` (no prompt cache between
# iterations). Pipeline runs themselves take 5-15 minutes per ticket; expect
# 10-20 tickets in an overnight session.
#
# Safety:
#   - Runs with --strict-mcp-config so the agent only sees the giga MCP server
#     for the specified board (no accidental access to other tools).
#   - The agent's only writes go through the MCP server (PR minter writes to the
#     *target* repo, not this one). Local working tree is untouched.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

BOARD=""
LABEL="auto-ready"
FAILED_LABEL="auto-failed"
CUSTOM_URL=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --board) BOARD="$2"; shift 2 ;;
    --label) LABEL="$2"; shift 2 ;;
    --mcp-url) CUSTOM_URL="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,40p' "$0"
      exit 0
      ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$BOARD" && -z "$CUSTOM_URL" ]]; then
  echo "ERROR: --board <id> required (or --mcp-url to override)" >&2
  exit 1
fi

# Derive MCP URL from board id. Mirror this with infra/config/boards.ts
# whenever boards are added or their subdomains change.
if [[ -n "$CUSTOM_URL" ]]; then
  MCP_URL="$CUSTOM_URL"
  BOARD="${BOARD:-custom}"
else
  case "$BOARD" in
    gigacorp-react)    MCP_URL="https://mcp.gigacorp.co/mcp" ;;
    pitchvault-react)  MCP_URL="https://mcp.pitchvault.co/mcp" ;;
    punch-tamagotchi)  MCP_URL="https://punch.gigacorp.co/mcp" ;;
    *)
      echo "ERROR: unknown board '$BOARD'. Add it to the case in $(basename "$0") or pass --mcp-url." >&2
      exit 1
      ;;
  esac
fi

STOP_FILE=".jira-ralph-${BOARD}.stop"
LOG_FILE="jira-ralph-${BOARD}.log"
MAX_ITERS="${JIRA_RALPH_MAX_ITERS:-50}"
SLEEP_BETWEEN="${JIRA_RALPH_SLEEP:-60}"

if ! command -v claude >/dev/null 2>&1; then
  echo "ERROR: claude CLI not on PATH" >&2
  exit 1
fi

# Clear any stale stop file from a previous run
rm -f "$STOP_FILE"

log() {
  printf '[%s] [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$BOARD" "$*" | tee -a "$LOG_FILE"
}

# Per-invocation MCP config so claude -p talks to exactly one server.
MCP_CONFIG="$(mktemp -t jira-ralph-mcp-XXXX.json)"
trap 'rm -f "$MCP_CONFIG"' EXIT

cat > "$MCP_CONFIG" <<EOF
{
  "mcpServers": {
    "giga": {
      "type": "http",
      "url": "$MCP_URL"
    }
  }
}
EOF

PROMPT=$(cat <<EOF
You are running inside an autonomous JIRA-driven loop iteration. You have access to ONE MCP server named "giga" connected to the "$BOARD" board.

YOUR JOB FOR THIS ITERATION — process EXACTLY ONE ticket and exit:

1. Call \`list_backlog(status="To Do", limit=50, unprocessed_only=true)\` to get candidates.
2. For each candidate (top to bottom): call \`get_ticket(issue_key)\` and check its labels.
3. Pick the FIRST candidate whose labels include "$LABEL". If none of the candidates carry that label, create the stop file by running this bash command: \`touch "$STOP_FILE"\`, print "NO_TICKETS", and exit. Do not do anything else.
4. Call \`process_ticket(issue_key=<key>, force=true, approve_plan=true)\` to start the pipeline end-to-end with no human gate.
   - If this call returns an error string (anything starting with "Pipeline not configured" or similar), skip to the failure branch in step 6.
5. Poll \`get_pipeline_status(issue_key=<key>)\` every 30 seconds via Bash sleep. Continue until the status reaches one of:
   - "complete" → success branch
   - "failed" or "error" → failure branch
   - 120 polls elapsed (~60 minutes) → failure branch with reason "timeout"
6. Apply outcome to JIRA:
   - SUCCESS: call \`edit_ticket(issue_key=<key>, labels=<existing labels MINUS "$LABEL">)\`. Print "ITER OK <key>".
   - FAILURE: call \`edit_ticket(issue_key=<key>, labels=<existing labels MINUS "$LABEL" PLUS "$FAILED_LABEL">)\`, then call \`add_comment(issue_key=<key>, body="Autonomous loop failed: <short reason>. Tagged $FAILED_LABEL for human triage.")\`. Print "ITER FAIL <key> <reason>".

HARD RULES:
- Process EXACTLY ONE ticket per invocation. The outer shell loop handles iteration; do NOT loop here.
- Never modify local files in the giga-mcp-server repo. All writes go through MCP tools.
- Never call \`process_ticket\` without both force=true AND approve_plan=true — the plan-only path is for humans.
- When editing labels, always preserve every existing label other than "$LABEL"/"$FAILED_LABEL". Read the ticket first, modify the list, then write it back.
EOF
)

iter=0
while [[ $iter -lt $MAX_ITERS ]]; do
  iter=$((iter + 1))

  if [[ -f "$STOP_FILE" ]]; then
    log "stop file present; exiting after $((iter - 1)) iterations"
    exit 0
  fi

  log "iter $iter: invoking claude (mcp=$MCP_URL)"
  if ! claude -p \
       --dangerously-skip-permissions \
       --strict-mcp-config \
       --mcp-config "$MCP_CONFIG" \
       "$PROMPT" 2>&1 | tee -a "$LOG_FILE"; then
    log "ERROR: claude exited non-zero; stopping loop"
    exit 1
  fi

  log "iter $iter: done, sleeping ${SLEEP_BETWEEN}s"
  sleep "$SLEEP_BETWEEN"
done

log "hit max iterations ($MAX_ITERS); exiting"
