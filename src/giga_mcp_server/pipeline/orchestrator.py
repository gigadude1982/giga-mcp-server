from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog

from giga_mcp_server.config import Board, Settings
from giga_mcp_server.jira.client import JiraClient
from giga_mcp_server.pipeline.agent_runner import AgentRunner
from giga_mcp_server.pipeline.github_tools import FileChange, GitHubClient
from giga_mcp_server.pipeline.jira_bridge import (
    add_pipeline_comment,
    get_ticket_for_pipeline,
    transition_ticket,
)
from giga_mcp_server.pipeline.repo_config import RepoConfig, load_repo_config

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# State model
# ---------------------------------------------------------------------------


@dataclass
class PipelineState:
    ticket_key: str
    status: str = "pending"  # pending | running | awaiting_approval | done | failed | halted
    branch: str = ""
    repo: str = ""  # github repo this run targets
    base_branch: str = ""  # github base branch for this board
    pr_url: str = ""
    pr_number: int = 0
    ci_state: str = ""
    error: str = ""
    stage: str = ""  # current/last stage name
    spec: dict[str, Any] = field(default_factory=dict)
    plan: dict[str, Any] = field(default_factory=dict)
    started_at: str = ""
    finished_at: str = ""

    def to_summary(self) -> str:
        lines = [
            f"**Ticket:** {self.ticket_key}",
            f"**Status:** {self.status}",
            f"**Stage:** {self.stage}",
        ]
        if self.repo:
            lines.append(f"**Repo:** {self.repo}")
        if self.branch:
            lines.append(f"**Branch:** {self.branch}")
        if self.pr_url:
            lines.append(f"**PR:** {self.pr_url}")
        if self.ci_state:
            lines.append(f"**CI:** {self.ci_state}")
        if self.error:
            lines.append(f"**Error:** {self.error}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class PipelineOrchestrator:
    """Runs the full autonomous implementation pipeline for a JIRA ticket."""

    def __init__(
        self,
        settings: Settings,
        jira_client: JiraClient,
    ) -> None:
        self._settings = settings
        self._jira = jira_client
        self._runner = AgentRunner(api_key=settings.anthropic_api_key)
        self._github_clients: dict[str, GitHubClient] = {}

    def _github_for(self, board: Board) -> GitHubClient:
        """Return a GitHubClient for the board's repo, building+caching on first use."""
        client = self._github_clients.get(board.github_repo)
        if client is None:
            client = GitHubClient(
                token=self._settings.github_token,
                repo=board.github_repo,
                commit_author_name=self._settings.pipeline_commit_author_name,
                commit_author_email=self._settings.pipeline_commit_author_email,
            )
            self._github_clients[board.github_repo] = client
        return client

    async def run(
        self,
        ticket_key: str,
        state: PipelineState,
        skip_human_gate: bool = False,
    ) -> PipelineState:
        """Execute the full pipeline. Mutates and returns the state object."""
        state.started_at = _now()
        state.status = "running"

        try:
            board = self._settings.board_for_issue(ticket_key)
            github = self._github_for(board)
            state.repo = board.github_repo
            state.base_branch = board.github_base_branch
            config = await load_repo_config(
                github,
                board.github_repo,
                board.github_base_branch,
                default_max_retries=self._settings.pipeline_max_retries,
            )
            await self._run_pipeline(
                ticket_key, state, config, github, skip_human_gate=skip_human_gate
            )
        except _HaltError as e:
            state.status = "halted"
            state.error = str(e)
            logger.warning("pipeline_halted", ticket=ticket_key, reason=str(e))
        except Exception as e:
            state.status = "failed"
            state.error = str(e)
            logger.exception("pipeline_failed", ticket=ticket_key)
        finally:
            state.finished_at = _now()

        return state

    async def _run_pipeline(
        self,
        ticket_key: str,
        state: PipelineState,
        config: RepoConfig,
        github: GitHubClient,
        skip_human_gate: bool = False,
    ) -> None:
        max_retries = config.max_retries_per_stage

        # ── Stage 1: Digest ──────────────────────────────────────────────
        state.stage = "digester"
        ticket_data = await get_ticket_for_pipeline(self._jira, ticket_key)
        spec = await _with_retry(
            lambda: self._runner.run("digester", ticket_data), max_retries, "digester"
        )

        if spec.get("clarification_needed"):
            questions = spec.get("clarification_questions", [])
            comment = (
                "Autonomous pipeline halted — clarification needed before implementation:\n\n"
                + "\n".join(f"- {q}" for q in questions)
            )
            await add_pipeline_comment(self._jira, ticket_key, comment)
            raise _HaltError(f"Clarification needed: {questions}")

        state.spec = spec
        logger.info("stage_complete", stage="digester", ticket=ticket_key)

        # ── Stage 2: Plan ────────────────────────────────────────────────
        state.stage = "planner"
        existing_files = await github.list_files(branch=state.base_branch)
        # Fetch content of likely-affected files (best-effort)
        relevant_contents = await self._fetch_relevant_files(
            spec.get("affected_areas", []),
            existing_files,
            github,
            state.base_branch,
        )

        plan_input = {
            "spec": spec,
            "existing_files": existing_files,
            "relevant_file_contents": relevant_contents,
            "coding_standards": config.coding_standards,
            "test_framework": config.test_framework,
        }
        plan = await _with_retry(
            lambda: self._runner.run("planner", plan_input), max_retries, "planner"
        )
        state.plan = plan
        logger.info("stage_complete", stage="planner", ticket=ticket_key)

        # ── Human gate ───────────────────────────────────────────────────
        if config.human_gate_after_planner and not skip_human_gate:
            state.status = "awaiting_approval"
            state.stage = "awaiting_approval"
            comment = _format_plan_comment(ticket_key, spec, plan)
            await add_pipeline_comment(self._jira, ticket_key, comment)
            logger.info("pipeline_awaiting_approval", ticket=ticket_key)
            # The pipeline pauses here; resumption requires calling run_from_plan()
            return

        await self._run_from_plan(ticket_key, state, config, github, spec, plan)

    async def run_from_plan(self, ticket_key: str, state: PipelineState) -> PipelineState:
        """Resume a pipeline that was paused at the human gate."""
        if state.status != "awaiting_approval":
            state.error = f"Cannot resume: status is {state.status!r}, expected 'awaiting_approval'"
            state.status = "failed"
            return state

        state.status = "running"
        try:
            board = self._settings.board_for_issue(ticket_key)
            github = self._github_for(board)
            state.repo = board.github_repo
            state.base_branch = board.github_base_branch
            config = await load_repo_config(
                github,
                board.github_repo,
                board.github_base_branch,
                default_max_retries=self._settings.pipeline_max_retries,
            )
            await self._run_from_plan(ticket_key, state, config, github, state.spec, state.plan)
        except _HaltError as e:
            state.status = "halted"
            state.error = str(e)
        except Exception:
            state.status = "failed"
            logger.exception("pipeline_resume_failed", ticket=ticket_key)
        finally:
            state.finished_at = _now()

        return state

    async def _run_from_plan(
        self,
        ticket_key: str,
        state: PipelineState,
        config: RepoConfig,
        github: GitHubClient,
        spec: dict[str, Any],
        plan: dict[str, Any],
    ) -> None:
        max_retries = config.max_retries_per_stage

        # ── Create branch ────────────────────────────────────────────────
        base_branch_name = f"{config.branch_prefix}{ticket_key.lower()}"
        branch_name = await self._make_branch(base_branch_name, github, state.base_branch)
        state.branch = branch_name
        logger.info("branch_created", branch=branch_name, ticket=ticket_key)

        # ── Transition Jira → In Progress ────────────────────────────────
        ok = await transition_ticket(self._jira, ticket_key, "In Progress")
        logger.info("jira_transition", ticket=ticket_key, status="In Progress", ok=ok)

        # ── Stage 3: Implement + Test (parallel) ─────────────────────────
        state.stage = "implementing"
        impl_files = [f for f in plan.get("files_to_modify", [])]
        test_file_specs = plan.get("test_files", [])

        # Fetch existing file contents for context
        all_file_specs = impl_files + test_file_specs
        existing_contents = await self._fetch_file_contents(
            [f["path"] for f in all_file_specs],
            branch=state.base_branch,
            github=github,
        )

        # Build implementer tasks
        impl_tasks = [
            self._run_implementer(f, plan, spec, existing_contents, config, max_retries)
            for f in impl_files
        ]
        # Build test writer tasks
        test_tasks = [
            self._run_test_writer(t, spec, existing_contents, config, max_retries)
            for t in test_file_specs
        ]

        results = await asyncio.gather(*impl_tasks, *test_tasks)
        impl_outputs = list(results[: len(impl_tasks)])
        test_outputs = list(results[len(impl_tasks) :])

        all_impl_outputs = impl_outputs + test_outputs
        logger.info(
            "stage_complete",
            stage="implementing",
            ticket=ticket_key,
            files=len(all_impl_outputs),
        )

        # ── Stage 4: Validate ────────────────────────────────────────────
        state.stage = "validator"
        impl_map = {o["path"]: o["content"] for o in impl_outputs}
        test_map = {o["path"]: o["content"] for o in test_outputs}

        validation = await _with_retry(
            lambda: self._runner.run(
                "validator",
                {
                    "spec": spec,
                    "implementation_files": impl_map,
                    "test_files": test_map,
                },
            ),
            max_retries,
            "validator",
        )

        if not validation.get("passed"):
            issues = validation.get("issues", [])
            raise _HaltError(f"Validation failed: {'; '.join(issues)}")

        logger.info("stage_complete", stage="validator", ticket=ticket_key)

        # ── Stage 5: PR Minter ───────────────────────────────────────────
        state.stage = "pr_minter"
        files_changed = [o["path"] for o in all_impl_outputs]
        minted = await _with_retry(
            lambda: self._runner.run(
                "pr_minter",
                {
                    "spec": spec,
                    "plan": plan,
                    "files_changed": files_changed,
                    "validator_summary": validation.get("summary", ""),
                    "ticket_key": ticket_key,
                },
            ),
            max_retries,
            "pr_minter",
        )
        logger.info("stage_complete", stage="pr_minter", ticket=ticket_key)

        # ── Commit all files atomically ──────────────────────────────────
        state.stage = "committing"
        file_changes = [
            FileChange(
                path=o["path"],
                content=o["content"],
                action=next(
                    (f.get("action", "modify") for f in impl_files if f["path"] == o["path"]),
                    "modify",
                ),
            )
            for o in all_impl_outputs
        ]
        await github.commit_changes(
            branch=branch_name,
            files=file_changes,
            message=minted["commit_message"],
        )
        logger.info("committed", branch=branch_name, files=len(file_changes))

        # ── Open PR ──────────────────────────────────────────────────────
        state.stage = "opening_pr"
        pr = await github.open_pull_request(
            branch=branch_name,
            title=minted["pr_title"],
            body=minted["pr_body"],
            base_branch=state.base_branch,
        )
        state.pr_url = pr.url
        state.pr_number = pr.number

        # ── Transition Jira → In Review ──────────────────────────────────
        ok = await transition_ticket(self._jira, ticket_key, "In Review")
        logger.info("jira_transition", ticket=ticket_key, status="In Review", ok=ok)
        await add_pipeline_comment(self._jira, ticket_key, minted["jira_comment"])

        # ── Poll CI ──────────────────────────────────────────────────────
        state.stage = "waiting_for_ci"
        ci_status = await github.poll_pr_until_complete(pr.number)
        state.ci_state = ci_status.state

        if ci_status.state == "failure":
            logger.warning("ci_failed", pr=pr.number, failed=ci_status.failed, ticket=ticket_key)
            await add_pipeline_comment(
                self._jira,
                ticket_key,
                f"CI failed on PR #{pr.number}. Failed checks: "
                f"{', '.join(ci_status.failed)}\nPR: {pr.url}",
            )
            state.status = "failed"
            state.error = f"CI checks failed: {ci_status.failed}"
            return

        state.status = "done"
        state.stage = "done"
        logger.info(
            "pipeline_complete",
            ticket=ticket_key,
            pr=pr.url,
            ci=ci_status.state,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _make_branch(self, base_name: str, github: GitHubClient, base_branch: str) -> str:
        """Create a branch, appending a datestamp if the base name already exists."""
        try:
            return await github.create_branch(base_name, from_branch=base_branch)
        except Exception:
            from datetime import date

            stamped = f"{base_name}-{date.today().strftime('%Y%m%d')}"
            return await github.create_branch(stamped, from_branch=base_branch)

    async def _run_implementer(
        self,
        file_spec: dict[str, Any],
        plan: dict[str, Any],
        spec: dict[str, Any],
        existing_contents: dict[str, str],
        config: RepoConfig,
        max_retries: int,
    ) -> dict[str, Any]:
        path = file_spec["path"]
        # Provide related files as context (exclude the file being implemented)
        related = {k: v for k, v in existing_contents.items() if k != path}

        input_data = {
            "path": path,
            "action": file_spec.get("action", "modify"),
            "reason": file_spec.get("reason", ""),
            "plan_approach": plan.get("approach", ""),
            "spec": spec,
            "existing_content": existing_contents.get(path, ""),
            "related_files": related,
            "coding_standards": config.coding_standards,
        }
        return await _with_retry(
            lambda: self._runner.run("implementer", input_data), max_retries, f"implementer:{path}"
        )

    async def _run_test_writer(
        self,
        test_spec: dict[str, Any],
        spec: dict[str, Any],
        existing_contents: dict[str, str],
        config: RepoConfig,
        max_retries: int,
    ) -> dict[str, Any]:
        path = test_spec["path"]
        input_data = {
            "path": path,
            "covers": test_spec.get("covers", []),
            "spec": spec,
            "test_framework": config.test_framework,
            "implementation_contents": {
                k: v for k, v in existing_contents.items() if not k.startswith("tests/")
            },
            "existing_test_content": existing_contents.get(path, ""),
            "coding_standards": config.coding_standards,
        }
        return await _with_retry(
            lambda: self._runner.run("test_writer", input_data), max_retries, f"test_writer:{path}"
        )

    async def _fetch_relevant_files(
        self,
        affected_areas: list[str],
        all_files: list[str],
        github: GitHubClient,
        base_branch: str,
    ) -> dict[str, str]:
        """Fetch contents of files that likely match the affected areas."""
        relevant = []
        for area in affected_areas:
            area_lower = area.lower().replace(".", "/")
            for f in all_files:
                if area_lower in f.lower() and f not in relevant:
                    relevant.append(f)

        # Cap at 10 files to keep context manageable
        relevant = relevant[:10]
        return await self._fetch_file_contents(relevant, base_branch, github)

    async def _fetch_file_contents(
        self, paths: list[str], branch: str, github: GitHubClient
    ) -> dict[str, str]:
        """Fetch multiple files concurrently. Skips files that don't exist."""

        async def _get(path: str) -> tuple[str, str]:
            try:
                content = await github.get_file(path, branch)
                return path, content
            except Exception:
                return path, ""

        pairs = await asyncio.gather(*(_get(p) for p in paths))
        return {k: v for k, v in pairs if v}


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


class _HaltError(Exception):
    """Raised to stop the pipeline without marking it as an unexpected failure."""


async def _with_retry(coro_fn: Any, max_retries: int, stage: str) -> Any:
    """Call an async factory function, retrying on exception."""
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return await coro_fn()
        except Exception as e:
            last_error = e
            logger.warning("stage_retry", stage=stage, attempt=attempt, error=str(e))
    raise RuntimeError(
        f"Stage {stage!r} failed after {max_retries} attempts: {last_error}"
    ) from last_error


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _format_plan_comment(ticket_key: str, spec: dict[str, Any], plan: dict[str, Any]) -> str:
    files = plan.get("files_to_modify", [])
    file_lines = "\n".join(f"- `{f['path']}` ({f['action']}): {f['reason']}" for f in files)
    risks = "\n".join(f"- {r}" for r in plan.get("risks", []))
    return (
        f"🤖 **Autonomous pipeline — plan ready for approval** ({ticket_key})\n\n"
        f"**Approach:** {plan.get('approach', '')}\n\n"
        f"**Files to change:**\n{file_lines}\n\n"
        f"**Risks:**\n{risks or '(none identified)'}\n\n"
        "To proceed with implementation, call `process_ticket` with "
        f"`ticket_key={ticket_key!r}` and `approve_plan=True`."
    )
