# Bitbucket (and GitLab) Support — design spike

Spike doc for making the VCS host pluggable so a board can point at GitHub, Bitbucket Cloud, or GitLab instead of being GitHub-only. Companion to [`PLANE-SUPPORT.md`](./PLANE-SUPPORT.md) — together they make every "external system" axis pluggable. Not committed to a timeline. Delete this file once shipped or dropped.

Started 2026-05-28.

## Goal

A board entry in `infra/config/boards.ts` should be able to say:

```ts
{
  boardId: "punch-tamagotchi",
  vcsType: "bitbucket",                     // ← new field
  vcsBaseUrl: "https://api.bitbucket.org/2.0", // or self-hosted Server URL
  vcsRepo: "gigadude1982/punch-tamagotchi",    // workspace/slug for BB, owner/repo for GH/GL
  vcsBaseBranch: "main",
  ...
}
```

…and the pipeline (`commit_changes` / `open_pull_request` / `poll_pr_until_complete`) Just Works against Bitbucket or GitLab instead of GitHub. **Per-board** choice. JIRA + Bitbucket is the most common Atlassian-shop combo and is the v1 target.

## Current GitHub surface (what the protocol has to cover)

All GitHub-specific code lives in `src/giga_mcp_server/pipeline/github_tools.py:GitHubClient` (≈500 LOC). Public methods used elsewhere:

| Method                                                | Purpose                                               | BB Cloud equivalent                                  | GL equivalent                                      |
| ----------------------------------------------------- | ----------------------------------------------------- | ---------------------------------------------------- | -------------------------------------------------- |
| `create_branch(name, from_branch)`                    | New ref from existing head SHA                        | `POST /repositories/{w}/{r}/refs/branches`           | `POST /projects/{id}/repository/branches`          |
| `get_file(path, branch)`                              | Read file content at ref                              | `GET /repositories/{w}/{r}/src/{rev}/{path}`         | `GET /projects/{id}/repository/files/{path}/raw`   |
| `list_files(path, branch)`                            | Tree listing                                          | `GET /repositories/{w}/{r}/src/{rev}/{path}`         | `GET /projects/{id}/repository/tree?path=`         |
| `commit_changes(branch, files, message)`              | **Atomic multi-file commit** — see catches            | `POST /repositories/{w}/{r}/src` (multipart form)    | `POST /projects/{id}/repository/commits` (actions) |
| `open_pull_request(title, body, head, base)`          | New PR                                                | `POST /repositories/{w}/{r}/pullrequests`            | `POST /projects/{id}/merge_requests`               |
| `get_pr_status(pr_number) → ChecksStatus`             | CI state for the head SHA                             | `GET /repositories/.../commit/{sha}/statuses`        | `GET /projects/{id}/repository/commits/{sha}/statuses` |
| `get_failed_check_logs(pr_number, max_chars)`         | Surface CI failures into validator feedback           | Bitbucket Pipelines logs API (BB Cloud)              | GitLab Jobs API (`/jobs/{id}/trace`)               |
| `list_merged_prs(...)`                                | Code-history vector store ingest                      | `GET /repositories/.../pullrequests?state=MERGED`    | `GET /projects/{id}/merge_requests?state=merged`   |
| `get_pr(pr_number)`                                   | Read PR metadata + attached files                     | `GET /repositories/.../pullrequests/{id}` + `/diff`  | `GET /projects/{id}/merge_requests/{iid}/changes`  |
| `poll_pr_until_complete(...)`                         | Polling wrapper over `get_pr_status`                  | wrapper over status calls                            | wrapper over status calls                          |

Internal helpers `_create_blob` / `_create_tree` / `_create_commit` / `_update_ref` are **GitHub-specific** — they implement atomic multi-file commits via the [Git Data API](https://docs.github.com/en/rest/git). These have no direct equivalent on Bitbucket Cloud (which uses a single multipart `POST /src` for all files in one commit) and a *better* one on GitLab (the Commits API takes an `actions[]` array natively).

Files that import `GitHubClient`:
- `src/giga_mcp_server/server.py` (constructs it in `lifespan`)
- `src/giga_mcp_server/pipeline/orchestrator.py` (consumes it through the pipeline)
- `src/giga_mcp_server/vector/code_history_ingest.py` (ingests merged-PR diffs for the vector store)

The pipeline prompts (`pipeline/agent_prompts.py`) do **not** mention GitHub by name — they talk about "pull requests" and "branches" which translate cleanly. One less de-vendor-ing pass than the Plane work needs.

## Proposed abstraction

### Step 1 — extract `VcsClient` Protocol

```python
# src/giga_mcp_server/vcs/protocol.py
from typing import Protocol
from dataclasses import dataclass

@dataclass
class FileChange:
    path: str
    content: str
    action: str  # "create", "modify", "delete"

@dataclass
class PullRequest:
    number: int
    url: str
    branch: str
    checks_url: str = ""

@dataclass
class ChecksStatus:
    state: str  # "pending", "success", "failure", "error"
    passed: list[str]
    failed: list[str]
    pending: list[str]

class VcsClient(Protocol):
    async def create_branch(self, name: str, from_branch: str = "main") -> str: ...
    async def get_file(self, path: str, branch: str) -> str: ...
    async def list_files(self, path: str = "", branch: str = "main") -> list[str]: ...
    async def commit_changes(self, branch: str, files: list[FileChange], message: str) -> str: ...
    async def open_pull_request(self, title: str, body: str, head: str, base: str) -> PullRequest: ...
    async def get_pr_status(self, pr_number: int) -> ChecksStatus: ...
    async def get_failed_check_logs(self, pr_number: int, max_chars: int = 3000) -> str: ...
    async def list_merged_prs(self, since_days: int = 30, max_results: int = 50) -> list[dict]: ...
    async def get_pr(self, pr_number: int) -> dict: ...
    async def poll_pr_until_complete(self, pr_number: int, timeout_s: int = 600) -> ChecksStatus: ...
```

Move `GitHubClient` to `src/giga_mcp_server/vcs/github.py`. The dataclasses `FileChange` / `PullRequest` / `ChecksStatus` already at the top of `github_tools.py` move to the protocol module unchanged.

### Step 2 — implement `BitbucketCloudVcsClient`

```python
# src/giga_mcp_server/vcs/bitbucket.py
class BitbucketCloudVcsClient:
    def __init__(self, settings: Settings) -> None:
        self._base = "https://api.bitbucket.org/2.0"
        self._workspace, self._slug = settings.vcs_repo.split("/", 1)
        self._auth = (settings.vcs_username, settings.vcs_app_password)
        # httpx.AsyncClient with basic auth
```

Bitbucket Cloud API ([reference](https://developer.atlassian.com/cloud/bitbucket/rest/intro/)) is well-shaped but **the commit model is unusual**:

```python
# Single endpoint POST /repositories/{w}/{r}/src — multipart form:
#   {filepath}: <content>     ← for each file to create/modify
#   files: <path>             ← repeated for each file to delete
#   message: "commit msg"
#   branch: "feat/foo"
#   parents: "<parent-sha>"
```

This is **atomic** (one HTTP request = one commit) but the lack of a Git Data API means:
- No way to compose tree → commit → ref like on GitHub
- File contents must be inline in the multipart body (no blob upload step)
- Binary files need base64-encoded content in a separate field

It's *simpler* than GitHub's 4-call flow, just very different. The wrapper should hide the difference entirely.

### Step 3 — implement `GitlabVcsClient` (likely free, see "Why also GitLab")

```python
# src/giga_mcp_server/vcs/gitlab.py
class GitlabVcsClient:
    def __init__(self, settings: Settings) -> None:
        self._base = settings.vcs_base_url.rstrip("/") + "/api/v4"
        self._project_id = settings.vcs_project_id  # numeric or url-encoded path
        self._headers = {"PRIVATE-TOKEN": settings.vcs_api_token}
```

GitLab's Commits API takes an `actions[]` array of `{action, file_path, content}` — directly maps to our `FileChange[]`. Easier than both GitHub and Bitbucket.

```python
# POST /projects/{id}/repository/commits
{
  "branch": "feat/foo",
  "commit_message": "...",
  "actions": [
    {"action": "create",  "file_path": "src/foo.ts", "content": "..."},
    {"action": "delete",  "file_path": "src/legacy.ts"}
  ]
}
```

### Step 4 — factory + config

In `config.py`:

```python
class Settings:
    vcs_type: Literal["github", "bitbucket", "gitlab"] = "github"
    vcs_base_url: str = ""        # empty → cloud default per type
    vcs_repo: str                 # owner/repo (GH) | workspace/slug (BB) | path or numeric id (GL)
    vcs_base_branch: str = "main"
    vcs_api_token: str            # was github_token
    vcs_username: str = ""        # BB Cloud needs username+app password; others ignore
```

Keep `github_*` aliases for one release so the SSM rename can be staged.

In `server.py:lifespan`:

```python
from giga_mcp_server.vcs import build_vcs_client
vcs = build_vcs_client(settings)  # picks GH / BB / GL by settings.vcs_type
```

In `infra/config/boards.ts`:

```ts
interface BoardConfig {
  vcsType?: "github" | "bitbucket" | "gitlab";  // defaults to "github"
  // existing fields renamed: githubRepo → vcsRepo, githubBaseBranch → vcsBaseBranch
}
```

### Step 5 — CI / webhook parity

This is where the real divergence lives.

- **`.github/workflows/jira-done-on-merge.yml`** is GitHub-Actions-only. Bitbucket equivalent is a **Bitbucket Pipeline** + webhook to the JIRA REST API, or — much cleaner — a **JIRA Automation rule** that watches Bitbucket commits/PRs natively (Atlassian-to-Atlassian integration is free and built-in). Recommend the JIRA Automation route for any Bitbucket board.
- **GitLab equivalent**: a `.gitlab-ci.yml` job triggered on `merge_request_event` close, or a Pipeline schedule.
- **CI status fetch** (`get_pr_status`, `get_failed_check_logs`): all three platforms have build-status APIs but the shape differs. The protocol's `ChecksStatus` shape is already neutral; each impl normalizes into it.
- **Bender's own CI** (`.github/workflows/ci.yml`, `deploy.yml`) stays on GitHub Actions — this is a "where does the *target* repo live" axis, not a "where does Bender's source live" axis. Bender itself stays on GitHub.

### Step 6 — auth differences

| Platform        | Auth method                                        | SSM key                                          |
| --------------- | -------------------------------------------------- | ------------------------------------------------ |
| GitHub          | PAT (Bearer token), `repo` + `workflow` scopes     | `/giga-mcp-server/{board}/github-token`          |
| Bitbucket Cloud | Username + App Password, basic auth                | `/giga-mcp-server/{board}/bitbucket-username`, `…/bitbucket-app-password` |
| Bitbucket Server | PAT (Bearer or basic)                              | `/giga-mcp-server/{board}/bitbucket-token`       |
| GitLab          | PAT, `api` + `write_repository` scopes             | `/giga-mcp-server/{board}/gitlab-token`          |

Bitbucket Cloud is the awkward one — two-secret auth. The `setup-ssm.sh` script needs a per-VCS conditional or a small switch case.

## Catches / known unknowns

- **Bitbucket Cloud's `/src` endpoint requires the parent SHA**, otherwise you get a "fast-forward only" error on concurrent commits. The current GitHub flow handles this implicitly via the Git Data API; the BB wrapper has to fetch the parent SHA before each commit.
- **Bitbucket Cloud rate limits** are stricter than GitHub's (1000 req/hour for app passwords). The pipeline's polling intervals (`_POLL_INTERVAL = 10`) may be too aggressive — bump to 20s for BB Cloud or use Bitbucket's `wait_for` PR status endpoint where available.
- **Bitbucket Server (Data Center / self-hosted)** has a *different* API (`/rest/api/1.0/`) from Bitbucket Cloud. Treat as a separate impl (`BitbucketServerVcsClient`) when needed. v1 = Cloud only.
- **GitLab project IDs are numeric OR URL-encoded paths.** The wrapper should accept either form in `vcs_repo` and normalize internally.
- **PR/MR numbering.** GitHub uses sequential numbers per repo. Bitbucket Cloud uses sequential per repo. GitLab uses both internal ID (`id`) and per-project IID (`iid`) — the public-facing one is `iid`. Wrapper must use IID consistently.
- **Commit author identity.** GitHub accepts `author: {name, email}` in the Git Data API. Bitbucket Cloud derives author from the authenticated user — can't override per-commit without raw git push. GitLab accepts `author_name` + `author_email` on the Commits API. The `commit_author_name`/`commit_author_email` settings only have an effect on GH and GL.
- **Branch protection** model differs across all three. The pipeline doesn't enforce it today (it expects the *target* repo to have its own protection rules), so this is informational, not blocking.
- **Code-history vector store ingest** (`code_history_ingest.py`) walks merged PRs and extracts diffs. Each VCS impl needs a `list_merged_prs` + per-PR diff fetch. BB Cloud and GitLab both have diff endpoints; shape differs.

## Out of scope for v1

- **Bitbucket Server / Data Center** self-hosted. Cloud only.
- **Gitea / Forgejo / Codeberg.** Possible after Bitbucket+GitLab land — they share the GitHub API shape closely so the GitHub impl can probably handle them with a base-URL override.
- **Cross-VCS code-history.** Embeddings stay namespaced per board, so this falls out for free — no shared vector index between, say, a GitHub board and a Bitbucket board.
- **Migration tooling** (move a board's history from GitHub→Bitbucket). Tracker-side data lives in JIRA/Plane; VCS-side history is on the platform. Not Bender's job.

## Why also GitLab

The user asked specifically about Bitbucket. Including GitLab in this spike because:
1. **GitLab's API is the easiest of the three.** Implementing it costs maybe a half-day on top of the Bitbucket work.
2. **It catches the Atlassian-shop combo plus the Linux-foundation/OSS-project combo with one design.** If only Bitbucket gets implemented, we'll regret it the first time someone wants GitLab.
3. **GitLab's `actions[]` Commits API is the cleanest of the three**, so building it first might actually clarify the protocol shape before tackling Bitbucket's quirky `/src` endpoint.

If timeline pressure mounts, drop GitLab from v1 and ship as v1.1. The architecture supports it either way.

## Phasing

| Phase                                          | Effort   | Risk                                                  |
| ---------------------------------------------- | -------- | ----------------------------------------------------- |
| 1. Extract `VcsClient` protocol                | ~1 day   | Low — pure refactor, all existing tests should pass   |
| 2. Implement `BitbucketCloudVcsClient`         | ~1.5 days| Medium — `/src` quirks, two-secret auth, parent SHA   |
| 3. Implement `GitlabVcsClient` (optional v1.1) | ~0.5 day | Low — Commits API maps directly to `FileChange[]`     |
| 4. Factory + config + SSM key rename           | ~0.5 day | Low — keep `github_*` aliases for one release         |
| 5. `setup-ssm.sh` BB two-secret handling       | ~0.5 day | Low                                                   |
| 6. CI/webhook parity guide (doc, not code)     | ~0.5 day | Low — point users at JIRA Automation for BB+JIRA      |
| 7. Integration test against a real BB Cloud repo| ~1 day   | Medium — first real Bitbucket round-trip              |

**Total: 4-5 days** for GitHub + Bitbucket Cloud working. Add ~half a day for GitLab.

## Combined with `PLANE-SUPPORT.md`

If both VCS and tracker axes go pluggable together, the most-requested combos light up:

| Tracker | VCS              | Use case                                              |
| ------- | ---------------- | ----------------------------------------------------- |
| JIRA    | GitHub           | Today's default (gigacorp, pitchvault, punch)         |
| JIRA    | Bitbucket Cloud  | Atlassian-shop standard                               |
| Plane   | GitHub           | OSS / indie devs, fully free stack                    |
| Plane   | GitLab           | Self-hosted everything (Plane self-host + GitLab CE)  |
| JIRA    | GitLab           | Enterprise + GitLab CI                                |

The two abstractions are independent — they can ship in either order, and any combo works.

## Demo angle (interview pitch)

"The board ↔ infra side is already pluggable. The tracker and VCS sides aren't — they're load-bearing on JIRA + GitHub. Here are two design spikes for the abstractions: ~3-4 days for the tracker, ~4-5 days for the VCS. Combined, that opens the matrix above. I chose to ship the multi-tenant infra first because the *infra* axis has the highest blast radius — getting that wrong would mean rebuilding the deployment story, which is hard to walk back. Tracker and VCS pluggability is wrappers over external APIs, which is reversible."

That's the layered-design story the interviewers will pattern-match on: *I built the harder axis first, the reversible ones can wait.*
