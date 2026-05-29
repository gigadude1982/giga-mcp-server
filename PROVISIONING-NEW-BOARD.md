# Provisioning a new board

End-to-end runbook for bringing up a new board on giga-mcp-server. A "board" is one JIRA project ↔ GitHub repo pair, deployed as a dedicated App Runner service behind its own subdomain with its own Cognito user pool and SSM secret tree.

This runbook captures every step. The README's [Adding a new board](README.md#adding-a-new-board) section links here.

**Time estimate:** 30–60 minutes if you have all credentials handy, longer if you need to create a JIRA project or wait on DNS propagation.

**Steps marked `[user]` require you to do something outside this repo (create a resource, paste a secret, click a button). Steps marked `[code]` are file edits in this repo (or the target repo).**

---

## Prerequisites

Before starting, gather:

- **JIRA**: Atlassian admin access for the org where the project will live, and an [API token](https://id.atlassian.com/manage-profile/security/api-tokens) for the JIRA user the pipeline will impersonate.
- **GitHub**: An account that can create the target repo, and a [classic personal access token](https://github.com/settings/tokens) with `repo` + `workflow` scopes for the pipeline. (Fine-grained tokens also work — needs read/write on Contents, Pull requests, Actions, and Workflows of the target repo.)
- **Anthropic**: An API key. The same key works across boards; billing is per-call.
- **Pinecone** (optional, only if `vectorEnabled: true`): An API key + the ability to create a new index in the workspace.
- **AWS**: Credentials that can `cdk deploy` to the account, write to SSM in the target region (`us-east-1`), and update Route 53 records for the parent domain.
- **DNS**: Access to the DNS zone for the subdomain you'll point at the new App Runner service.

---

## Step-by-step

### 1. `[user]` Choose identifiers and lock them in

Pick once, write down — these get referenced in `boards.ts`, SSM key paths, log messages, and DNS records. Hard to change later.

| Thing                                 | Example                              |
| ------------------------------------- | ------------------------------------ |
| Board ID (kebab-case)                 | `punch-tamagotchi`                   |
| MCP server name (Claude Desktop name) | `punch-mcp-server`                   |
| JIRA project key                      | `PUNCH`                              |
| GitHub repo                           | `gigadude1982/punch-tamagotchi`      |
| Public subdomain                      | `punch.gigacorp.co`                  |
| Pinecone index name                   | `punch-tickets`                      |

### 2. `[user]` Create the GitHub repo

- Create `<owner>/<repo>` on github.com.
- Initialize with `main` as the default branch.
- Initializing with an empty README is fine — the scaffold (step 4) will overwrite it via a merge commit.

### 3. `[user]` Create the JIRA project

- In the target Atlassian instance, create a JIRA project with the key from step 1.
- Standard scrum/kanban template is fine. Add a default "To Do / In Progress / Done" workflow.
- **Workflow statuses the pipeline needs** (`In Plan Review`, `In Development`, `In Code Review`) get auto-created by Bender on first run, but a JIRA admin still has to wire them into the workflow's transitions. Do this now while you're already in JIRA admin, or wait for Bender to log the hint and do it later. Either works.

### 4. `[code]` Scaffold the target repo

Bender writes code into the target repo via the Git Data API — but the target repo needs to exist as a buildable, testable project before the first ticket. Minimum:

- A working build (`npm run build` or `pytest` or whatever your language is)
- A test runner the pipeline can invoke (`npm test`, `pytest`, etc.)
- A `.giga-pipeline.json` at the repo root (see below)
- Optional but recommended: `.prettierrc` / `.eslintrc` / `.editorconfig` — Bender auto-inlines these into the implementer's coding-standards context

**Minimum `.giga-pipeline.json`** (target repo root):

```json
{
  "language": "javascript",
  "test_framework": "jest",
  "test_command": "npm test -- --watchAll=false",
  "source_dirs": ["src"],
  "test_dirs": ["src"],
  "branch_prefix": "auto/"
}
```

Adjust per the target stack. Full schema documented in [README → Repo pipeline config](README.md#repo-pipeline-config-optional).

**For React + PWA targets**, see [`PUNCH-TAMAGOTCHI-PLAN.md`](PUNCH-TAMAGOTCHI-PLAN.md) for a worked example (Vite + React + TS + vite-plugin-pwa + Jest), and the `gigadude1982/punch-tamagotchi` repo as a reference scaffold.

### 5. `[code]` Add the board to `infra/config/boards.ts`

Append a new entry to the `BOARDS` array:

```ts
{
  boardId: "punch-tamagotchi",
  serverName: "punch-mcp-server",
  jiraProjectKey: "PUNCH",
  jiraUrl: "https://gigacorporation.atlassian.net",
  jiraUsername: "admin@gigacorp.co",
  githubRepo: "gigadude1982/punch-tamagotchi",
  githubBaseBranch: "main",
  subdomain: "punch.gigacorp.co",
  vectorEnabled: true,                  // optional; enables Pinecone duplicate detection
  pineconeIndexName: "punch-tickets",   // required if vectorEnabled
},
```

Open a PR, get it reviewed, merge to `main`. Don't `cdk deploy` yet — the SSM secrets aren't in place.

**Validate locally before opening the PR**:

```bash
cd infra && npx cdk synth
```

Look for `Service<boardId>...` outputs in the synthesized template (App Runner ARN, default URL, Cognito user pool ID, app client ID). All four should appear.

### 6. `[user]` Create `.env.<boardId>` locally

In the giga-mcp-server repo root (not the target repo), create `.env.<boardId>` with the secrets the pipeline needs. **This file is gitignored — never commit it.**

```bash
# .env.punch-tamagotchi
GIGA_JIRA_URL=https://gigacorporation.atlassian.net
GIGA_JIRA_USERNAME=admin@gigacorp.co
GIGA_JIRA_API_TOKEN=<atlassian API token>
GIGA_JIRA_PROJECT_KEY=PUNCH

GIGA_GITHUB_TOKEN=<github PAT with repo+workflow scopes>
GIGA_GITHUB_REPO=gigadude1982/punch-tamagotchi

GIGA_ANTHROPIC_API_KEY=sk-ant-...

# Required if vectorEnabled: true in boards.ts
GIGA_PINECONE_API_KEY=pcsk_...
```

### 7. `[user]` Push secrets to SSM

```bash
./scripts/setup-ssm.sh --board <boardId>
```

This reads `.env.<boardId>` and writes each secret as a SecureString to `/giga-mcp-server/<boardId>/<key>` in us-east-1. Look for `OK` lines for each of the four parameters. If the script's default loop doesn't include your board yet, the `--board` filter is the right way to invoke it — and consider also adding the board to the default loop in `scripts/setup-ssm.sh` as a polish PR.

**Verify**:

```bash
aws ssm describe-parameters \
  --parameter-filters "Key=Name,Option=BeginsWith,Values=/giga-mcp-server/<boardId>" \
  --region us-east-1
```

You should see four parameters: `jira-api-token`, `anthropic-api-key`, `github-token`, `pinecone-api-key`.

### 8. `[user]` `cdk deploy`

```bash
cd infra
npx cdk deploy
```

Single stack — this updates everything in one shot. New board gets a new App Runner service, new Cognito pool, new IAM roles. Existing services are untouched (CDK is incremental).

Capture two outputs from the deploy summary:

- `Service<boardId>DefaultUrl` — the App Runner-assigned URL, e.g. `https://abc123.us-east-1.awsapprunner.com`. This is the CNAME target for the next step.
- `Service<boardId>CognitoUserPoolId` / `…AppClientId` — needed if you want OAuth-protected MCP connections.

### 9. `[user]` Configure DNS

In the DNS zone for your parent domain (e.g. `gigacorp.co` in Route 53 or wherever your DNS lives):

- Add a `CNAME` record: `<subdomain>` → the App Runner default URL from step 8 (strip the `https://`).
- TTL 300 is fine for initial setup; bump to 3600 once stable.

App Runner's custom domain association will also provision an ACM certificate automatically — wait ~5 minutes for it to validate, then the subdomain should serve the new service.

**Verify**:

```bash
curl -I https://<subdomain>/health
```

Look for `200 OK` with the App Runner health response.

### 10. `[user]` Add Bitbucket / GitHub auto-close workflow to target repo

Copy `.github/workflows/jira-done-on-merge.yml` from giga-mcp-server to the new target repo (in `.github/workflows/`). Add three secrets to the target repo's GitHub Actions secrets:

- `JIRA_URL` (or `JIRA_BASE_URL` — the workflow has a fallback chain)
- `JIRA_USERNAME`
- `JIRA_API_TOKEN`

Once added, merging any PR with a JIRA key in the title or body (`PUNCH-1`, `[PUNCH-42]`, etc.) will auto-transition the ticket to `Done`.

> Note: this is GitHub-Actions-specific. For Bitbucket or GitLab targets, see [`BITBUCKET-SUPPORT.md`](BITBUCKET-SUPPORT.md) for the alternative (JIRA Automation rules work cleaner than a custom Pipeline).

### 11. `[code]` File the first ticket and run the pipeline

In JIRA, create the first ticket (`<KEY>-1`). Keep the scope small — the pipeline does best on tightly scoped tickets. Example for a new React+PWA target:

> "Scaffold game shell with idle placeholder sprite, hunger meter, and feed button. Persist hunger state in localStorage. Add Testing Library coverage for the feed button."

Then, from a configured Bender instance (Claude Desktop pointing at the new server, or `giga-mcp-server` over stdio):

```
process_ticket(issue_key="<KEY>-1")
```

The pipeline runs Digester + Planner, posts the plan to JIRA, and pauses. Read the plan. If it's good:

```
process_ticket(issue_key="<KEY>-1", approve_plan=True)
```

Watch the PR get opened, CI run, and the ticket transition to `In Code Review`.

---

## Troubleshooting

- **`cdk deploy` fails with "SSM parameter not found"**: you skipped step 7 or the `--board` filter didn't match. Re-run `./scripts/setup-ssm.sh --board <boardId>` and re-deploy.
- **App Runner stays in `OPERATION_IN_PROGRESS` for >10 min**: usually means the Docker image is missing for the board's `:latest` tag. Check the GitHub Actions deploy workflow ran successfully on the last `main` push.
- **JIRA transition fails with "transition not found"**: the pipeline tried to move the ticket to `In Plan Review` / `In Development` / `In Code Review` and the workflow doesn't have that status wired in. Add the transition in JIRA admin → workflow editor.
- **Pipeline opens a PR but CI fails immediately**: target repo doesn't have a working build/test setup. Fix the scaffold (step 4) and re-run `process_ticket(..., force=True)`.
- **DNS works but Bender server returns 502**: App Runner can't reach SSM. Check the App Runner service role's IAM permissions; CDK should provision this correctly, but a manual edit to the stack can break it.
- **Pinecone "namespace not found"**: the index exists but the `<boardId>` namespace hasn't been seeded yet. Run the `backfill_vectors` MCP tool once to populate it from existing JIRA tickets.

---

## What gets provisioned per board

For each board entry, `cdk deploy` creates:

- **App Runner service** named `giga-mcp-<boardId>-service`, pulling `:latest` from the shared ECR repo
- **Cognito user pool** for OAuth (if `enableAuth: true`)
- **Cognito app client** for the user pool
- **IAM role** for the App Runner service with `ssm:GetParameter` scoped to `/giga-mcp-server/<boardId>/*`
- **Custom domain association** between App Runner and the subdomain (ACM cert auto-managed)
- **CloudWatch log group** `/aws/apprunner/giga-mcp-<boardId>-service`

Shared across all boards:

- **ECR repo** `giga-mcp-server` (one Docker image for everything; per-board behavior driven by env vars from SSM)
- **Pinecone account** (per-board indexes are namespaced, not separate accounts)
- **Anthropic API key** (per-call billing makes sharing fine)
- **AWS account + region** (`us-east-1`)

A pre-interview system-design doc is planned to capture the full topology with diagrams — see `interview_prep_todos.md` if curious.
