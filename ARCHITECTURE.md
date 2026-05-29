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
pair**, all provisioned by a single CDK stack. Adding a board is a one-line config change.

---

## System at a glance

```
Claude Desktop / claude.ai ──(MCP over streamable-http, Cognito-auth'd)──> App Runner service (per board)
                                                                              │
                          ┌───────────────────────────────────────────────────┤
                          ▼                                                     ▼
                  Enrichment (Haiku)                              Autonomous pipeline (per-stage models)
                  - analyze/enrich ticket                         Digester → Planner →[Impl ∥ Tests]→ Validator ↺ → PR Minter
                  - dup detection (Pinecone)                      - writes code via GitHub Git Data API (atomic commit)
                          │                                       - grounded in code-history (merged-PR memory)
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
> means one tenant can't see or break another, and onboarding is a config line + `cdk deploy`.

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
Digester → Planner → [Implementers ∥ Test Writers] → Validator ↺ → PR Minter
                          ↑________ retry on validator fail (capped) ________|
```

- **Digester** normalizes the raw ticket into a structured spec.
- **Planner** emits the file list, approach, and test strategy — anchored on the repo's
  **existing files + relevant file contents** (this is why it's a *feature-addition* engine,
  not a greenfield scaffolder — see decisions).
- **Implementers / Test Writers** run in parallel; both are additionally **grounded in
  code-history** (similar past merged PRs).
- **Validator** checks implementation ↔ test coherence and reviews the diff **statically**.
  On blocking issues it feeds them back to the Implementer and retries, up to
  `GIGA_PIPELINE_MAX_RETRIES`.
- **PR Minter** writes the PR title/body/commit message.

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

**Execution feedback via CI.** The Validator's review is *static* (it reasons over the diff,
not a running program), but the pipeline doesn't stop there: after the PR is opened it **polls
the PR's GitHub Actions CI** (`poll_pr_until_complete`), and on failure it **fetches the
failure logs, feeds them back to the Implementer/Test-Writer, re-validates, commits a fix to
the same branch, and re-polls** — a bounded build/test feedback loop with **CI as the execution
environment**. If it's still red after the fix cycle, it comments "manual review required" on
the ticket. The important caveat: **this only engages if the target repo has CI** that builds
and tests PRs; with no CI there are no checks to fail, so the loop stays dormant.

**Two-call `process_ticket` flow** (gated by `GIGA_PIPELINE_HUMAN_GATE`):
1. `process_ticket(issue_key)` → Digester + Planner, posts plan to JIRA, status
   `awaiting_approval`.
2. `process_ticket(issue_key, approve_plan=True)` → resumes from the saved plan, runs
   Implementer/Test/Validator/PR Minter. `force=True` reprocesses terminal tickets;
   `force=True, approve_plan=True` skips the gate end-to-end.

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
- **CI as the execution sandbox vs. a local pre-PR build.** The Validator is static, but the
  pipeline gets real build/test signal by polling the PR's CI and feeding failures back into a
  bounded fix-and-recommit loop — execution without the server needing to run a working tree.
  Trade-off: it's *post-PR* (a failing PR is opened, then fixed) and **depends on the target
  repo having CI**. A local/pre-PR build sandbox could catch failures before the PR is opened,
  at the cost of running untrusted generated code on the server — deliberately avoided for now.
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

- **Execution validation depends on target-repo CI** — the pipeline already polls CI and
  fixes on failure, but it only engages if the repo *has* CI building/testing PRs (punch-pwa
  currently has none). *Plan:* ship a CI workflow with each board's repo; optionally add a
  pre-PR local build sandbox so failures are caught before the PR is opened.
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
React-flavored agent prompts, JIRA workflow statuses needing manual transitions, Pinecone
opt-in + backfill, etc.).
