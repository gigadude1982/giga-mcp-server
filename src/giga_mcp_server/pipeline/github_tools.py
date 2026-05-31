from __future__ import annotations

import asyncio
import base64
import re
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

logger = structlog.get_logger()

# CI-log distillation. GitHub Actions logs are huge and prefixed with ISO
# timestamps; naive keyword-greping surfaces a wall of dependency stack frames
# (`at Object.foo (node_modules/...)`) and buries the one line the implementer
# needs (the tsc error, the assertion diff, the RTL "Found multiple elements").
# These patterns extract the actionable failure blocks instead.
_TS_PREFIX = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.]+Z\s")
# Noise to drop outright: stack frames and GitHub Actions runner ceremony.
_LOG_NOISE = re.compile(
    r"(^\s*at\s|node_modules|Post job cleanup|Temporarily overriding HOME|"
    r"Adding repository directory|/usr/bin/git|^git version|^\[command\]|"
    r"##\[(group|endgroup)\])"
)
# Lines that start a meaningful failure block — keep these plus trailing context.
_LOG_HEADER = re.compile(
    r"(●\s|^\s*FAIL\s|error TS\d|✕|✗|×|AssertionError|[A-Za-z]*Error:\s|"
    r"Found multiple elements|Unable to find|is not assignable|"
    r"Cannot find name|Property '|\d+:\d+\s+error|\bFAILED\b)"
)
# Standalone signal lines worth keeping even without a following block.
_LOG_SIGNAL = re.compile(
    r"(Expected|Received|Test Suites:|Tests:|SyntaxError|TypeError|ReferenceError)"
)


def _distill_log(text: str, *, max_lines: int = 140, context: int = 6) -> str:
    """Pull the actionable lines out of a raw CI job log.

    Strips ISO timestamps, drops node_modules stack frames, and keeps each
    failure header plus a few following lines (so the assertion/diff/render
    that explains a failure survives). Collapses blank runs.
    """
    kept: list[str] = []
    ctx_remaining = 0
    for raw in text.splitlines():
        line = _TS_PREFIX.sub("", raw).rstrip()
        if _LOG_NOISE.search(line):
            continue
        if _LOG_HEADER.search(line):
            kept.append(line)
            ctx_remaining = context
        elif ctx_remaining > 0:
            kept.append(line)
            ctx_remaining -= 1
        elif _LOG_SIGNAL.search(line):
            kept.append(line)
        if len(kept) >= max_lines:
            break

    collapsed: list[str] = []
    for line in kept:
        if not line.strip() and (collapsed and not collapsed[-1].strip()):
            continue
        collapsed.append(line)
    return "\n".join(collapsed).strip()

# TODO: consider replacing with github mcp server if cleaner
# or SDK e.g. from github import Github
# and move to github module to mirror jira client structure. For now, this is a minimal custom client

_GH_API = "https://api.github.com"
_POLL_INTERVAL = 10  # seconds between CI status polls
_POLL_TIMEOUT = 600  # seconds before giving up on CI
_NO_CHECKS_GRACE = 30  # seconds to wait for any check to register before assuming none


@dataclass
class FileChange:
    """A single file to include in an atomic commit."""

    path: str
    content: str  # raw string content; empty string = delete
    action: str = "modify"  # "modify", "create", or "delete"


@dataclass
class PullRequest:
    number: int
    url: str
    branch: str
    checks_url: str = ""
    node_id: str = ""  # GraphQL global ID; required to mark a draft PR ready
    draft: bool = False


@dataclass
class ChecksStatus:
    state: str  # "pending", "success", "failure", "error"
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)


class GitHubClient:
    """Async GitHub client using the Git Data API for atomic commits."""

    def __init__(
        self,
        token: str,
        repo: str,
        commit_author_name: str = "giga-pipeline[bot]",
        commit_author_email: str = "giga-pipeline[bot]@users.noreply.github.com",
    ) -> None:
        """
        Args:
            token: GitHub personal access token (needs repo + workflow scopes).
            repo:  Owner/repo string, e.g. "daltonbruce/giga-mcp-server".
            commit_author_name:  Display name for pipeline commits.
            commit_author_email: Email for pipeline commits.
        """
        self._repo = repo
        self._commit_author_name = commit_author_name
        self._commit_author_email = commit_author_email
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    # ------------------------------------------------------------------
    # Branch operations
    # ------------------------------------------------------------------

    async def create_branch(self, branch_name: str, from_branch: str = "main") -> str:
        """Create a new branch from an existing one. Returns the new branch name."""
        base_sha = await self._get_branch_sha(from_branch)
        url = f"{_GH_API}/repos/{self._repo}/git/refs"
        payload = {"ref": f"refs/heads/{branch_name}", "sha": base_sha}
        async with httpx.AsyncClient(headers=self._headers) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
        logger.info(
            "branch_created",
            repo=self._repo,
            branch=branch_name,
            from_branch=from_branch,
        )
        return branch_name

    async def _get_branch_sha(self, branch: str) -> str:
        url = f"{_GH_API}/repos/{self._repo}/git/ref/heads/{branch}"
        async with httpx.AsyncClient(headers=self._headers) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()["object"]["sha"]

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    async def get_file(self, path: str, branch: str) -> str:
        """Fetch a file's decoded text content from a branch."""
        url = f"{_GH_API}/repos/{self._repo}/contents/{path}"
        async with httpx.AsyncClient(headers=self._headers) as client:
            resp = await client.get(url, params={"ref": branch})
            resp.raise_for_status()
            data = resp.json()
            return base64.b64decode(data["content"]).decode()

    async def list_files(self, path: str = "", branch: str = "main") -> list[str]:
        """Recursively list all file paths under a directory on a branch."""
        url = f"{_GH_API}/repos/{self._repo}/git/trees/{branch}"
        async with httpx.AsyncClient(headers=self._headers) as client:
            resp = await client.get(url, params={"recursive": "1"})
            resp.raise_for_status()
            tree = resp.json().get("tree", [])

        prefix = path.rstrip("/") + "/" if path else ""
        return [
            item["path"]
            for item in tree
            if item["type"] == "blob" and item["path"].startswith(prefix)
        ]

    # ------------------------------------------------------------------
    # Atomic commit via Git Data API
    # ------------------------------------------------------------------

    async def commit_changes(
        self,
        branch: str,
        files: list[FileChange],
        message: str,
    ) -> str:
        """Atomically commit multiple file changes to a branch.

        Uses the Git Data API (blob → tree → commit → ref update) so all
        changes land in a single commit with no intermediate states.

        Returns the new commit SHA.
        """
        # 1. Get the current HEAD commit + tree SHA
        head_sha = await self._get_branch_sha(branch)
        base_tree_sha = await self._get_commit_tree_sha(head_sha)

        # 2. Create blobs for modified/created files
        tree_entries = []
        async with httpx.AsyncClient(headers=self._headers) as client:
            for fc in files:
                if fc.action == "delete":
                    # Deletion: include path with null SHA
                    tree_entries.append(
                        {
                            "path": fc.path,
                            "mode": "100644",
                            "type": "blob",
                            "sha": None,
                        }
                    )
                else:
                    blob_sha = await self._create_blob(client, fc.content)
                    tree_entries.append(
                        {
                            "path": fc.path,
                            "mode": "100644",
                            "type": "blob",
                            "sha": blob_sha,
                        }
                    )

            # 3. Create a new tree
            new_tree_sha = await self._create_tree(client, base_tree_sha, tree_entries)

            # 4. Create the commit
            new_commit_sha = await self._create_commit(
                client, message, new_tree_sha, head_sha
            )

            # 5. Update the branch ref
            await self._update_ref(client, branch, new_commit_sha)

        logger.info(
            "commit_created",
            repo=self._repo,
            branch=branch,
            files=len(files),
            sha=new_commit_sha[:8],
        )
        return new_commit_sha

    async def _get_commit_tree_sha(self, commit_sha: str) -> str:
        url = f"{_GH_API}/repos/{self._repo}/git/commits/{commit_sha}"
        async with httpx.AsyncClient(headers=self._headers) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()["tree"]["sha"]

    async def _create_blob(self, client: httpx.AsyncClient, content: str) -> str:
        url = f"{_GH_API}/repos/{self._repo}/git/blobs"
        resp = await client.post(url, json={"content": content, "encoding": "utf-8"})
        resp.raise_for_status()
        return resp.json()["sha"]

    async def _create_tree(
        self,
        client: httpx.AsyncClient,
        base_tree_sha: str,
        entries: list[dict[str, Any]],
    ) -> str:
        url = f"{_GH_API}/repos/{self._repo}/git/trees"
        resp = await client.post(
            url, json={"base_tree": base_tree_sha, "tree": entries}
        )
        resp.raise_for_status()
        return resp.json()["sha"]

    async def _create_commit(
        self,
        client: httpx.AsyncClient,
        message: str,
        tree_sha: str,
        parent_sha: str,
    ) -> str:
        url = f"{_GH_API}/repos/{self._repo}/git/commits"
        author = {"name": self._commit_author_name, "email": self._commit_author_email}
        resp = await client.post(
            url,
            json={
                "message": message,
                "tree": tree_sha,
                "parents": [parent_sha],
                "author": author,
                "committer": author,
            },
        )
        resp.raise_for_status()
        return resp.json()["sha"]

    async def _update_ref(
        self, client: httpx.AsyncClient, branch: str, sha: str
    ) -> None:
        url = f"{_GH_API}/repos/{self._repo}/git/refs/heads/{branch}"
        resp = await client.patch(url, json={"sha": sha})
        resp.raise_for_status()

    # ------------------------------------------------------------------
    # Pull request operations
    # ------------------------------------------------------------------

    async def open_pull_request(
        self,
        branch: str,
        title: str,
        body: str,
        base_branch: str = "main",
        draft: bool = False,
    ) -> PullRequest:
        """Open a pull request. Returns PullRequest with number and URL.

        draft: open as a draft PR. Used by the CI-gate flow so generated code
               can be run through real CI before the PR is marked ready for
               review. Convert to ready via mark_pr_ready() once CI is green.
        """
        url = f"{_GH_API}/repos/{self._repo}/pulls"
        payload = {
            "title": title,
            "body": body,
            "head": branch,
            "base": base_branch,
            "draft": draft,
        }
        async with httpx.AsyncClient(headers=self._headers) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()

        pr = PullRequest(
            number=data["number"],
            url=data["html_url"],
            branch=branch,
            checks_url=data.get("statuses_url", ""),
            node_id=data.get("node_id", ""),
            draft=data.get("draft", draft),
        )
        logger.info("pr_opened", repo=self._repo, pr=pr.number, url=pr.url, draft=pr.draft)
        return pr

    async def update_pull_request(
        self, pr_number: int, *, title: str | None = None, body: str | None = None
    ) -> None:
        """Update a PR's title and/or body. Used to swap the provisional draft
        title for the final minted PR text once CI is green."""
        payload: dict[str, str] = {}
        if title is not None:
            payload["title"] = title
        if body is not None:
            payload["body"] = body
        if not payload:
            return
        url = f"{_GH_API}/repos/{self._repo}/pulls/{pr_number}"
        async with httpx.AsyncClient(headers=self._headers) as client:
            resp = await client.patch(url, json=payload)
            resp.raise_for_status()
        logger.info("pr_updated", repo=self._repo, pr=pr_number)

    async def mark_pr_ready(self, node_id: str) -> None:
        """Mark a draft PR ready for review.

        The REST API cannot toggle draft→ready, so this uses the GraphQL
        markPullRequestReadyForReview mutation. node_id is the PR's GraphQL
        global ID (PullRequest.node_id).
        """
        if not node_id:
            logger.warning("mark_pr_ready_skipped", reason="no node_id")
            return
        query = (
            "mutation($id:ID!){markPullRequestReadyForReview(input:{pullRequestId:$id})"
            "{pullRequest{isDraft}}}"
        )
        async with httpx.AsyncClient(headers=self._headers) as client:
            resp = await client.post(
                f"{_GH_API}/graphql",
                json={"query": query, "variables": {"id": node_id}},
            )
            resp.raise_for_status()
            data = resp.json()
        if data.get("errors"):
            raise RuntimeError(f"mark_pr_ready failed: {data['errors']}")
        logger.info("pr_marked_ready", repo=self._repo)

    async def get_pr_status(
        self, pr_number: int, head_sha: str | None = None
    ) -> ChecksStatus:
        """Get the current CI check status for a PR.

        head_sha: pin the check lookup to a specific commit. Pass the SHA
            returned by commit_changes after pushing — otherwise we read the
            PR's reported head.sha, which lags behind a Git Data API ref update
            by a few seconds and would surface the PREVIOUS commit's (failed)
            checks immediately after a fix push. check-runs are keyed by commit
            SHA, so pinning the SHA we just pushed eliminates that race.
        """
        async with httpx.AsyncClient(headers=self._headers) as client:
            if head_sha is None:
                resp = await client.get(f"{_GH_API}/repos/{self._repo}/pulls/{pr_number}")
                resp.raise_for_status()
                head_sha = resp.json()["head"]["sha"]

            # Fetch check runs for the pinned commit
            checks_url = f"{_GH_API}/repos/{self._repo}/commits/{head_sha}/check-runs"
            resp = await client.get(checks_url)
            resp.raise_for_status()
            runs = resp.json().get("check_runs", [])

        if not runs:
            return ChecksStatus(state="pending")

        passed, failed, pending = [], [], []
        for run in runs:
            name = run["name"]
            status = run["status"]
            conclusion = run.get("conclusion")

            if status != "completed":
                pending.append(name)
            elif conclusion in ("success", "skipped", "neutral"):
                passed.append(name)
            else:
                failed.append(name)

        if failed:
            state = "failure"
        elif pending:
            state = "pending"
        else:
            state = "success"

        return ChecksStatus(state=state, passed=passed, failed=failed, pending=pending)

    async def get_failed_check_logs(self, pr_number: int, max_chars: int = 6000) -> str:
        """Fetch stdout logs from failed CI check runs for a PR.

        Returns a distilled string of the failure output suitable for feeding
        back to the implementer as ci_failure_feedback. The distillation keeps
        the actionable failure blocks (tsc errors, assertion diffs, RTL "Found
        multiple elements" messages) and drops timestamp prefixes and
        node_modules stack frames so the model sees signal, not noise.
        """
        url = f"{_GH_API}/repos/{self._repo}/pulls/{pr_number}"
        async with httpx.AsyncClient(headers=self._headers) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            head_sha = resp.json()["head"]["sha"]

            checks_url = f"{_GH_API}/repos/{self._repo}/commits/{head_sha}/check-runs"
            resp = await client.get(checks_url)
            resp.raise_for_status()
            runs = resp.json().get("check_runs", [])

            failure_logs: list[str] = []
            for run in runs:
                if run.get("conclusion") not in ("failure", "timed_out"):
                    continue
                logs_url = run.get("details_url", "")
                jobs_url = f"{_GH_API}/repos/{self._repo}/actions/runs/{run['id']}/jobs"
                try:
                    resp = await client.get(jobs_url)
                    resp.raise_for_status()
                    for job in resp.json().get("jobs", []):
                        if job.get("conclusion") != "failure":
                            continue
                        log_url = f"{_GH_API}/repos/{self._repo}/actions/jobs/{job['id']}/logs"
                        log_resp = await client.get(log_url, follow_redirects=True)
                        if log_resp.status_code == 200:
                            distilled = _distill_log(log_resp.text)
                            if distilled:
                                failure_logs.append(f"Job: {job['name']}\n{distilled}")
                except Exception:
                    if logs_url:
                        failure_logs.append(f"Check: {run['name']} — see {logs_url}")

        combined = "\n\n".join(failure_logs)
        return combined[:max_chars] if combined else "CI failed — no log details available"

    # ------------------------------------------------------------------
    # PR introspection (used by code-history ingester)
    # ------------------------------------------------------------------

    async def list_merged_prs(
        self,
        since_days: int = 90,
        base_branch: str = "main",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """List merged PRs against base_branch within the last N days.

        Returns dicts with: number, title, body, merged_at, merge_commit_sha,
        url, files (list of paths). Files are fetched concurrently per PR.
        """
        from datetime import datetime, timedelta, timezone

        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=since_days)
        results: list[dict[str, Any]] = []
        page = 1

        async with httpx.AsyncClient(headers=self._headers, timeout=30.0) as client:
            while len(results) < limit:
                resp = await client.get(
                    f"{_GH_API}/repos/{self._repo}/pulls",
                    params={
                        "state": "closed",
                        "base": base_branch,
                        "sort": "updated",
                        "direction": "desc",
                        "per_page": "100",
                        "page": str(page),
                    },
                )
                resp.raise_for_status()
                page_data = resp.json()
                if not page_data:
                    break

                stop = False
                for pr in page_data:
                    merged_at = pr.get("merged_at")
                    if not merged_at:
                        continue
                    merged_dt = datetime.fromisoformat(
                        merged_at.replace("Z", "+00:00")
                    )
                    if merged_dt < cutoff:
                        stop = True
                        break
                    results.append(
                        {
                            "number": pr["number"],
                            "title": pr["title"],
                            "body": pr.get("body") or "",
                            "merged_at": merged_at,
                            "merge_commit_sha": pr.get("merge_commit_sha", ""),
                            "url": pr["html_url"],
                        }
                    )
                    if len(results) >= limit:
                        break
                if stop or len(page_data) < 100:
                    break
                page += 1

        async def _attach_files(pr: dict[str, Any]) -> dict[str, Any]:
            try:
                async with httpx.AsyncClient(headers=self._headers, timeout=30.0) as c:
                    resp = await c.get(
                        f"{_GH_API}/repos/{self._repo}/pulls/{pr['number']}/files",
                        params={"per_page": "100"},
                    )
                    resp.raise_for_status()
                    pr["files"] = [f["filename"] for f in resp.json()]
            except Exception:
                pr["files"] = []
            return pr

        return list(await asyncio.gather(*(_attach_files(pr) for pr in results)))

    async def get_pr(self, pr_number: int) -> dict[str, Any]:
        """Fetch a single PR plus its file list. Used by index_pr ingest."""
        async with httpx.AsyncClient(headers=self._headers, timeout=30.0) as client:
            pr_resp = await client.get(
                f"{_GH_API}/repos/{self._repo}/pulls/{pr_number}"
            )
            pr_resp.raise_for_status()
            pr = pr_resp.json()

            files_resp = await client.get(
                f"{_GH_API}/repos/{self._repo}/pulls/{pr_number}/files",
                params={"per_page": "100"},
            )
            files_resp.raise_for_status()
            files = [f["filename"] for f in files_resp.json()]

        return {
            "number": pr["number"],
            "title": pr["title"],
            "body": pr.get("body") or "",
            "merged_at": pr.get("merged_at") or "",
            "merge_commit_sha": pr.get("merge_commit_sha", ""),
            "url": pr.get("html_url", ""),
            "files": files,
            "merged": pr.get("merged_at") is not None,
        }

    async def get_pr_diff(
        self,
        pr_number: int,
        *,
        file_filter: str | None = None,
        max_chars: int = 3000,
    ) -> str:
        """Fetch the unified diff for a PR, optionally narrowed to a single file.

        Used by the hybrid code-history retrieval path — vector search finds
        the top-k similar PRs by summary, then this method pulls the actual
        patch so the agents can ground generation in real code, not summaries.

        file_filter: if given, return only the patch for that file. Returns
                     empty string when the PR didn't touch the file.
        max_chars:   hard cap on returned size. Truncates with a trailing
                     "... [truncated, N more chars]" marker so the model knows
                     it's not seeing the full diff. Set to 0 for no cap.
        """
        url = f"{_GH_API}/repos/{self._repo}/pulls/{pr_number}/files"
        try:
            async with httpx.AsyncClient(headers=self._headers, timeout=30.0) as client:
                resp = await client.get(url, params={"per_page": "100"})
                resp.raise_for_status()
                files = resp.json()
        except Exception as e:
            logger.warning("pr_diff_fetch_failed", pr=pr_number, error=str(e))
            return ""

        chunks: list[str] = []
        for f in files:
            filename = f.get("filename", "")
            if file_filter and filename != file_filter:
                continue
            patch = f.get("patch")
            if not patch:
                # Binary file or patch too large for GitHub to return inline
                continue
            chunks.append(f"=== {filename} ===\n{patch}")
            if file_filter:
                break  # Only one file wanted; stop after we find it

        diff = "\n\n".join(chunks)
        if max_chars and len(diff) > max_chars:
            remainder = len(diff) - max_chars
            diff = diff[:max_chars] + f"\n... [truncated, {remainder} more chars]"
        return diff

    async def poll_pr_until_complete(
        self,
        pr_number: int,
        timeout: int = _POLL_TIMEOUT,
        interval: int = _POLL_INTERVAL,
        no_checks_grace: int = _NO_CHECKS_GRACE,
        head_sha: str | None = None,
        require_checks: bool = False,
    ) -> ChecksStatus:
        """Poll PR checks until all complete or timeout is reached.

        head_sha: pin polling to a specific commit (see get_pr_status). Always
            pass the SHA returned by commit_changes so a just-pushed fix isn't
            judged against the previous commit's stale checks.
        require_checks: when True, never short-circuit to "none" — keep waiting
            for checks to register. Use after a fix push to a repo we already
            know has CI: checks for the new commit take a few seconds to appear,
            and bailing to "none" in that window would skip the gate. When
            False (the initial poll), a checkless repo still falls back to
            "none" after no_checks_grace instead of blocking to the timeout.
        """
        elapsed = 0
        while elapsed < timeout:
            status = await self.get_pr_status(pr_number, head_sha=head_sha)
            if status.state != "pending":
                logger.info(
                    "pr_checks_complete",
                    pr=pr_number,
                    state=status.state,
                    passed=len(status.passed),
                    failed=len(status.failed),
                )
                return status
            # No checks have registered yet. If we don't require them and that
            # persists past the grace period, treat the repo as having no PR CI
            # rather than waiting out the full timeout.
            no_checks_yet = not (status.passed or status.failed or status.pending)
            if no_checks_yet and not require_checks and elapsed >= no_checks_grace:
                logger.info("pr_no_checks", pr=pr_number, elapsed=elapsed)
                return ChecksStatus(state="none")
            logger.info(
                "pr_checks_pending", pr=pr_number, elapsed=elapsed,
                awaiting_checks=no_checks_yet,
            )
            await asyncio.sleep(interval)
            elapsed += interval

        logger.warning("pr_checks_timeout", pr=pr_number, timeout=timeout)
        return ChecksStatus(state="error", pending=["timeout"])
