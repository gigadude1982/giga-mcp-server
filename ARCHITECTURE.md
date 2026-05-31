# Architecture — giga-mcp-server (Bender)

A deep-dive into how Bender works, **why** it's built this way, and where it's going.
Read top-to-bottom for the full picture, or jump to [Key design decisions](#key-design-decisions--trade-offs)
for the rationale behind the choices.

---

## TL;DR

Bender is a **multi-tenant MCP server** that puts two AI subsystems in front of a JIRA
project + GitHub repo:

1. **Enrichment** — analyzes a ticket and improves it (priority, labels, acceptance
   criteria, subtasks, duplicate detection). Single-shot, cheap model.
2. **Autonomous implementation pipeline** — takes a ticket from natural language to an
   opened pull request: digest → plan → implement + test → validate (with a retry loop)
   → mint PR.

One codebase, one Docker image, **one App Runner service per JIRA-board ↔ GitHub-repo
pair**, all provisioned by a single CDK stack. Adding a board starts with a `BoardConfig`
entry, plus the board-specific SSM secrets and DNS setup.

---

## System at a glance

```
Claude Desktop / claude.ai ──(MCP over streamable-http, Cognito-auth'd)──> App Runner service (per board)
                                                                              │
                          ┌───────────────────────────────────────────────────┤
                          ▼                                                     ▼
                  Enrichment (Haiku)                              Autonomous pipeline (per-stage models)
                  - analyze/enrich ticket                         Digest→Plan→[Impl ∥ Tests]→pre-flight→draft PR→REAL CI ↺→ready
                  - dup detection (Pinecone)                      - writes code via GitHub Git Data API (atomic commit)
                          │                                       - language-aware rule packs + code-history grounding
                          ▼                                                     │
                     JIRA (REST)  <───── status transitions, comments, plan ───┘
                          ▲
   GitHub webhook (merged PR) ──> /webhooks/github ──> auto-ingest into code-history (Pinecone)
```

Everything is per-board: its own subdomain, Cognito user pool, SSM secrets, and (optionally)
Pinecone indexes.

---

## Multi-tenancy & deployment

- **One image, N services.** The same container runs for every board; behavior is driven
  entirely by env vars. A board is one entry in `infra/config/boards.ts`.
- **CDK single stack** (`infra/lib/giga-mcp-server-stack.ts`) loops over `BOARDS` and
  instantiates a `GigaMcpServerService` construct per board: App Runner service + Cognito
  user pool + app client + outputs. Shared ECR repo + IAM roles.
- **Deploy flow:** push to `main` → GitHub Actions builds the image and pushes `:latest`
  to ECR → App Runner auto-redeploys (`autoDeploymentsEnabled`). `cdk deploy` is only
  needed when *adding/changing a board or its env/secrets*, not for code changes.
- **Secrets** live in SSM (`/giga-mcp-server/<board>/*`) and are wired as App Runner
  `runtimeEnvironmentSecrets`; non-secret config as `runtimeEnvironmentVariables`.
- **Per-board feature flags** in `boards.ts`: `enableAuth`, `vectorEnabled`,
  `codeHistoryEnabled`, plus index names and a `cognitoPoolSuffix` escape hatch (used to
  force-recreate a pool after out-of-band drift).

> **Why this shape:** the brief is "evolve one app per team with minimal human
> involvement." A board = a team's app. Per-board isolation (separate pool/secrets/index)
> means one tenant can't see or break another, and onboarding is a `BoardConfig` entry plus SSM/DNS setup + `cdk deploy`.

---

## Subsystem 1 — Enrichment

Single Claude call (cheap `GIGA_ANTHROPIC_MODEL`, Haiku by default) that reads a ticket and
returns structured JSON: suggested priority/type/labels, acceptance criteria (Given/When/Then),
an expanded description, optional subtasks, and a possible duplicate. Applied back to JIRA.

- **Duplicate detection**: if `vectorEnabled`, semantic search over a Pinecone index of past
  tickets; otherwise a fuzzy-match fallback.
- **Description formatting**: agents emit Markdown, but JIRA's v2 API treats the description
  as **wiki markup** — so a `markdown_to_jira_wiki()` converter runs at the write boundary
  (headings → `h2.`, bullets, bold, code, links). Without it, `## headings` render as
  mangled nested ordered lists.

---

## Subsystem 2 — Autonomous pipeline

Defined as prompts + I/O JSON schemas in `pipeline/agent_prompts.py:AGENT_REGISTRY`, driven
by `pipeline/orchestrator.py`.

```
Digester → Planner → [Implementers ∥ Test Writers] → Validator (pre-flight filter)
                                                          ↓
                          commit → DRAFT PR → REAL GitHub Actions CI ↺ → mark ready → PR Minter
                                      ↑___fix from distilled CI logs (capped)___|
```

- **Digester** normalizes the raw ticket into a structured spec.
- **Planner** emits the file list, approach, and test strategy — anchored on the repo's
  **existing files + relevant file contents** (this is why it's a *feature-addition* engine,
  not a greenfield scaffolder — see decisions).
- **Implementers / Test Writers** run in parallel; both are grounded in **code-history**
  (similar past merged PRs) **and in language-aware rule packs** — see below.
- **Validator** is a **cheap one-shot pre-flight filter**, *not* the gate. It statically
  reviews the diff once (plus one corrective regeneration if it flags issues) so an obviously
  broken change doesn't waste a real CI run. Authoritative correctness is decided by CI.
- **PR Minter** writes the PR title/body/commit message.

**Language-aware rule packs (`pipeline/rule_packs.py`).** The agent prompts in
`agent_prompts.py` are stack-agnostic; the concrete per-language rules (typed props vs
PropTypes, `tsc` constraints, Jest's `global`→`globalThis`, formatter rules) live in rule
packs keyed by stack (`python` / `javascript-react` / `typescript-react`) and are appended to
the implementer/test_writer/validator system prompt at runtime. The stack is resolved from the
repo's `.giga-pipeline.json` `language` (or an explicit `stack` override); an unknown stack
falls back to no extra rules and relies on `coding_standards`. *This is why a TypeScript repo
no longer gets PropTypes-laden, non-compiling code while a JavaScript repo still does the
React-with-PropTypes thing it expects.*

**Per-stage model routing** — each agent carries a `model`; the runner resolves
`model_override > per-stage model > default`:

| Stage | Model | Why |
| --- | --- | --- |
| planner, implementer, validator | **Opus** | high-value reasoning / codegen / critique |
| digester, test_writer | **Sonnet** | structured extraction / mechanical tests |
| pr_minter, pr_summarizer | **Haiku** | trivial text |

A target repo's `.giga-pipeline.json` `pipeline_model` sets `model_override`, forcing all
stages onto one model (escape hatch). *(Gotcha we fixed: the override must be reset every
run — the orchestrator is a long-lived shared instance, so a stale override would leak across
tickets.)*

**Atomic commits.** Files land via the **GitHub Git Data API** as a single commit — no
intermediate states, no partial pushes.

**Real CI is the gate (`ci_gate`, default on).** The Validator's review is static, so it's
demoted to the pre-flight filter above; **authoritative correctness comes from real GitHub
Actions CI**. The flow (`orchestrator._run_ci_gate_flow`): commit → open a **draft** PR → poll
CI → on failure, **distill the failure logs** and feed them to the Implementer/Test-Writer,
commit a fix to the same branch, and re-poll — up to **`ci_max_attempts`** times (default 5,
separate from `max_retries_per_stage` which governs transient agent retries). When CI goes
green, the PR is **marked ready for review** and the ticket moves to *In Code Review*. Every
retry is driven by real build/test output, not a simulated review.

Three correctness details this loop depends on, each learned from a watched run:
- **Polling is pinned to the pushed commit SHA.** GitHub's `PR.head.sha` lags a Git Data API
  ref update by seconds; an unpinned poll would read the *previous* commit's (failed) checks
  immediately after a fix and burn every attempt in ~2s. Checks are keyed by SHA, so we pin it.
- **CI logs are distilled** (`_distill_log`) before feedback: timestamps and `node_modules`
  stack frames are stripped, and each failure header (tsc error, Jest `●`, assertion diff, RTL
  "Found multiple elements") is kept with context. Otherwise the Implementer fixes half-blind
  against a wall of stack frames.
- **A closed PR / no-CI repo is detected, not waited on.** A closed PR gets no `pull_request`
  CI on new commits, so the poller checks PR state and returns `closed` instead of hanging to
  the timeout. A repo with no PR CI at all returns `none` after a short grace and the run
  finalizes on the pre-flight verdict (`ci_gate: false` keeps the legacy validator-as-gate flow
  as an escape hatch).

**Two-call `process_ticket` flow** (gated by `GIGA_PIPELINE_HUMAN_GATE`):
1. `process_ticket(issue_key)` → Digester + Planner, posts plan to JIRA, status
   `awaiting_approval`.
2. `process_ticket(issue_key, approve_plan=True)` → resumes from the saved plan, runs
   Implement/Test → pre-flight Validator → draft PR → real-CI gate → mark ready → PR Minter.
   `force=True` reprocesses terminal tickets; `force=True, approve_plan=True` skips the gate
   end-to-end.

**State:** pipeline runs live in an in-memory `dict` (`AppContext.pipeline_runs`). It's
**not persisted** — a restart loses in-flight runs. Anything needing durability reads **JIRA
status** as the source of truth, not the in-memory dict.

---

## Long-term memory — vector store + code-history

Two Pinecone (integrated-inference) stores, both opt-in per board:

- **Ticket store** (`vectorEnabled`) — embeddings of processed tickets, for enrichment
  duplicate detection. Seeded by `backfill_tickets`; single ticket via `index_ticket`;
  auto-upserted during enrichment.
- **Code-history store** (`codeHistoryEnabled`) — Haiku-summarized **merged PRs**, queried
  at pipeline runtime by the Implementer + Validator to ground generation in how the codebase
  actually evolved. **Hybrid retrieval** (`code_history_hybrid`): vector finds the top-k
  similar PRs by summary, then the *actual GitHub diffs* are fetched so agents see real code,
  not lossy summaries.

**Ingestion is explicit** (no silent staleness): `backfill_code_history` (bulk),
`index_pr` (one), and — new — a **GitHub webhook** (`/webhooks/github`) that auto-ingests on
merge. The webhook is HMAC-verified (`X-Hub-Signature-256`, not Cognito — GitHub can't present
a bearer token; FastMCP's auth wraps only `/mcp`, so the custom route relies on the HMAC). On
a merge into the base branch for the configured repo, it fires `index_pr` in the background and
returns `202`. Tooling is symmetric: `backfill_tickets`/`index_ticket` (tickets) ↔
`backfill_code_history`/`index_pr` (PRs).

---

## Auth & security

- The server is an OAuth **resource server**: `_configure_auth` installs a
  `CognitoTokenVerifier` that validates bearer JWTs (`token_use: access`, `client_id` match)
  against the board's Cognito pool. `enableAuth` per board injects the pool/client env so the
  server enforces it; otherwise it runs open.
- **Desktop** connects via `mcp-remote` with a static `Authorization: Bearer <cognito-jwt>`
  header (helper: `scripts/launch-claude-desktop.sh`). Tokens are minted with
  `USER_PASSWORD_AUTH`.
- **Webhook** auth is independent (HMAC shared secret in SSM).
- The pipeline is a write-and-spend primitive (opens PRs, costs tokens), so unauthenticated
  exposure is a real risk — all boards are locked with `enableAuth: true`.

---

## Key design decisions & trade-offs

These are the "why X and not Y" questions worth being able to answer cold.

- **Critic-refine (split Implementer/Validator) vs. a single self-correcting "Ralph" loop.**
  Separate agents + a **hard retry cap** give a clean adversarial check and bounded cost/latency;
  a single agent grading its own work is a weaker critic and can loop unboundedly.
- **Per-stage model routing vs. one model.** Opus where reasoning/codegen quality pays off,
  cheaper tiers elsewhere — better cost/quality than blanket-Opus, and a stronger Implementer
  means fewer Validator retries (partly self-funding). One env flag still forces a single model.
- **Feature-addition engine vs. greenfield scaffolder.** The Planner anchors on existing
  files/conventions/PR-history; pointed at an empty repo it has nothing to anchor on and drifts.
  So the pattern is **establish a foundation by hand, then let the pipeline evolve it** — which
  also matches the target use case (an existing app). Greenfield (an "architect" stage that
  designs the skeleton first) is roadmap.
- **Real CI as the gate vs. trusting the static Validator.** Originally the Validator (a static
  LLM review) decided whether to open the PR. That was blind — it can't actually compile or run
  tests — so the loop guessed. Now **real GitHub Actions CI is the gate**: the change lands on a
  **draft** PR, the bounded loop fixes against *real* build/test output (distilled), and the PR
  is marked ready only when CI is green. The Validator is kept as a cheap pre-flight filter so a
  CI run isn't wasted on obviously broken output. Trade-off: it **depends on the target repo
  having CI** (handled — closed/no-CI cases short-circuit instead of hanging) and a fix cycle is
  a real CI run (minutes). A local/pre-PR build sandbox could compile before committing and skip
  CI round-trips, at the cost of running untrusted generated code on the server — that's the
  next optimization, not the current bottleneck.
- **Language-aware rule packs vs. one hardcoded stack.** The prompts were hardcoded
  JavaScript-React (PropTypes, JS Jest idioms) and produced non-compiling code on a TypeScript
  repo. Splitting the stack-specific rules into `rule_packs.py` keyed off `language` lets one
  pipeline serve JS and TS repos correctly; unknown stacks fall back to `coding_standards` only.
- **Bearer token vs. full OAuth connector.** Desktop bearer is simple and works today; the
  claude.ai/mobile native connector needs the OAuth authorization-code flow (Cognito hosted UI),
  and Cognito lacks Dynamic Client Registration — a real integration cost, deliberately deferred.
- **Webhook vs. CI action vs. polling for code-history ingest.** Webhook is real-time, needs no
  per-repo CI changes, and the server is already board-aware; polling is laggy, and a CI action
  needs creds + an MCP client in the workflow.
- **JIRA status as source of truth vs. persisting pipeline state.** In-memory state is simplest
  and survives the request; durability comes from JIRA transitions, so a restart is recoverable
  by reading the board.

---

## Known limitations & roadmap

Naming these (and the plan) is the point — it's what a senior candidate does.

- **Execution validation depends on target-repo CI** — real CI is now the gate and the fix
  loop runs against it, but it only engages if the repo *has* PR CI (closed/no-CI cases
  short-circuit cleanly rather than hang). *Plan:* a pre-PR **local build sandbox** that
  compiles/tests generated code before committing — skips CI round-trips and catches failures
  even on repos without CI. This is now an optimization, not a blocker: the language rule packs
  already get generated code compiling (validated end-to-end on punch-pwa: a TypeScript ticket
  taken to a CI-green, review-ready PR autonomously).
- **Feature-addition only, not greenfield** — *Plan:* an "architect" stage that designs the
  skeleton + conventions from requirements, then fans tickets against it.
- **Mobile / claude.ai connector** — needs Cognito hosted-UI OAuth + a DCR workaround (see
  `MOBILE-CONNECTOR-OAUTH.md`).
- **Code-history auto-ingest is per-board** — currently only on boards with `codeHistoryEnabled`
  (punch-pwa). Extending to others is a per-board enable.
- **Pipeline state is in-memory** — fine today (JIRA is the durable record); a store would be
  needed for cross-restart in-flight resumption.
- **Operational drift** — e.g. a Cognito pool deleted out-of-band left services pointing at a
  dead pool; recovered via a `cognitoPoolSuffix` force-replace. Lesson: treat CFN state as
  authoritative and watch for drift.

---

## Operational gotchas

See `CLAUDE.md` → "Things that bite" for the live list (version-bump on `pip install -e .`,
adding a rule pack for a new stack in `rule_packs.py`, JIRA workflow statuses needing manual
transitions, Pinecone opt-in + backfill, etc.).
