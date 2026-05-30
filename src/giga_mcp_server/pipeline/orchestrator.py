from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog

from giga_mcp_server.config import Settings
from giga_mcp_server.jira.client import JiraClient
from giga_mcp_server.pipeline.agent_runner import AgentRunner
from giga_mcp_server.pipeline.github_tools import FileChange, GitHubClient
from giga_mcp_server.pipeline.jira_bridge import (
    add_pipeline_comment,
    get_ticket_for_pipeline,
    transition_ticket,
)
from giga_mcp_server.pipeline.repo_config import RepoConfig, load_repo_config
from giga_mcp_server.vector import CodeHistoryStore

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# State model
# ---------------------------------------------------------------------------


@dataclass
class PipelineState:
    ticket_key: str
    status: str = "pending"          # pending | running | awaiting_approval | done | failed | halted
    branch: str = ""
    pr_url: str = ""
    pr_number: int = 0
    ci_state: str = ""
    error: str = ""
    stage: str = ""                  # current/last stage name
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
        if self.branch:
            lines.append(f"**Branch:** {self.branch}")
        if self.pr_url:
            lines.append(f"**PR:** {self.pr_url}")
        if self.ci_state:
            lines.append(f"**CI:** {self.ci_state}")
        if self.error:
            lines.append(f"**Error:** {self.error}")
        return "\n".join(lines)


@dataclass
class _GateContext:
    """Immutable-per-run inputs shared by the implement/validate/commit/PR
    helpers. Bundled so the two gate flows and their helpers take one arg
    instead of threading eight positional parameters through every call."""

    ticket_key: str
    state: PipelineState
    config: RepoConfig
    spec: dict[str, Any]
    plan: dict[str, Any]
    impl_files: list[dict[str, Any]]
    test_file_specs: list[dict[str, Any]]
    existing_contents: dict[str, str]
    past_review_signals: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class PipelineOrchestrator:
    """Runs the full autonomous implementation pipeline for a JIRA ticket."""

    def __init__(
        self,
        settings: Settings,
        jira_client: JiraClient,
        code_history: CodeHistoryStore | None = None,
    ) -> None:
        self._settings = settings
        self._jira = jira_client
        self._code_history = code_history
        self._runner = AgentRunner(api_key=settings.anthropic_api_key)
        self._config_model: str | None = None  # set after config is loaded
        self._github = GitHubClient(
            token=settings.github_token,
            repo=settings.github_repo,
            commit_author_name=settings.pipeline_commit_author_name,
            commit_author_email=settings.pipeline_commit_author_email,
        )

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
            config = await load_repo_config(
                self._github,
                self._settings.github_repo,
                self._settings.github_base_branch,
                default_max_retries=self._settings.pipeline_max_retries,
            )
            # Set (or CLEAR) the override every run. The orchestrator is a
            # long-lived shared instance, so assigning None when this repo has no
            # pipeline_model is required — otherwise a prior run's override leaks
            # in and forces every stage onto the wrong model.
            self._runner.model_override = config.pipeline_model
            await self._run_pipeline(ticket_key, state, config, skip_human_gate=skip_human_gate)
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
        skip_human_gate: bool = False,
    ) -> None:
        max_retries = config.max_retries_per_stage

        # ── Stage 1: Digest ──────────────────────────────────────────────
        state.stage = "digester"
        ticket_data, backlog_examples = await asyncio.gather(
            get_ticket_for_pipeline(self._jira, ticket_key),
            self._fetch_backlog_examples(exclude_key=ticket_key),
        )
        if backlog_examples:
            ticket_data["backlog_examples"] = backlog_examples
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
        existing_files = await self._github.list_files(
            branch=self._settings.github_base_branch
        )
        # Fetch content of likely-affected files and formatter configs concurrently
        relevant_contents, formatter_configs = await asyncio.gather(
            self._fetch_relevant_files(spec.get("affected_areas", []), existing_files),
            self._fetch_formatter_configs(self._settings.github_base_branch),
        )
        if formatter_configs:
            config.coding_standards = (
                f"{config.coding_standards}\n\nFormatter configs from repo:\n{formatter_configs}"
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
            ok = await transition_ticket(self._jira, ticket_key, "In Plan Review")
            logger.info("jira_transition", ticket=ticket_key, status="In Plan Review", ok=ok)
            logger.info("pipeline_awaiting_approval", ticket=ticket_key)
            # The pipeline pauses here; resumption requires calling run_from_plan()
            return

        await self._run_from_plan(ticket_key, state, config, spec, plan)

    async def run_from_plan(
        self, ticket_key: str, state: PipelineState
    ) -> PipelineState:
        """Resume a pipeline that was paused at the human gate."""
        if state.status != "awaiting_approval":
            state.error = f"Cannot resume: status is {state.status!r}, expected 'awaiting_approval'"
            state.status = "failed"
            return state

        state.status = "running"
        try:
            config = await load_repo_config(
                self._github,
                self._settings.github_repo,
                self._settings.github_base_branch,
                default_max_retries=self._settings.pipeline_max_retries,
            )
            formatter_configs = await self._fetch_formatter_configs(
                self._settings.github_base_branch
            )
            if formatter_configs:
                config.coding_standards = (
                    f"{config.coding_standards}\n\nFormatter configs from repo:\n{formatter_configs}"
                )
            # Set (or CLEAR) the override every run. The orchestrator is a
            # long-lived shared instance, so assigning None when this repo has no
            # pipeline_model is required — otherwise a prior run's override leaks
            # in and forces every stage onto the wrong model.
            self._runner.model_override = config.pipeline_model
            await self._run_from_plan(ticket_key, state, config, state.spec, state.plan)
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
        spec: dict[str, Any],
        plan: dict[str, Any],
    ) -> None:
        # ── Create branch ────────────────────────────────────────────────
        base_branch_name = f"{config.branch_prefix}{ticket_key.lower()}"
        branch_name = await self._make_branch(base_branch_name)
        state.branch = branch_name
        logger.info("branch_created", branch=branch_name, ticket=ticket_key)

        # ── Transition Jira → In Development ─────────────────────────────
        ok = await transition_ticket(self._jira, ticket_key, "In Development")
        logger.info("jira_transition", ticket=ticket_key, status="In Development", ok=ok)

        impl_files = [f for f in plan.get("files_to_modify", [])]
        test_file_specs = plan.get("test_files", [])

        # Fetch existing file contents for context
        all_file_specs = impl_files + test_file_specs
        existing_contents = await self._fetch_file_contents(
            [f["path"] for f in all_file_specs],
            branch=self._settings.github_base_branch,
        )

        # Long-term memory: similar past PRs as a calibration signal for the
        # validator. Spec is immutable for this run, so fetch once.
        # Validator uses a smaller per-hit diff cap because it gets 5 hits.
        past_review_signals = await self._fetch_history(
            query_text=spec.get("summary", ""),
            limit=5,
            hybrid=config.code_history_hybrid,
            diff_chars_per_hit=config.code_history_diff_chars_per_hit // 2,
        )

        ctx = _GateContext(
            ticket_key=ticket_key,
            state=state,
            config=config,
            spec=spec,
            plan=plan,
            impl_files=impl_files,
            test_file_specs=test_file_specs,
            existing_contents=existing_contents,
            past_review_signals=past_review_signals,
        )

        if config.ci_gate:
            await self._run_ci_gate_flow(ctx)
        else:
            await self._run_validator_gate_flow(ctx)

    # ------------------------------------------------------------------
    # Gate flows
    # ------------------------------------------------------------------

    async def _run_ci_gate_flow(self, ctx: _GateContext) -> None:
        """CI-as-gate flow.

        The LLM validator runs ONCE as a cheap pre-flight filter (plus one
        corrective pass) so we don't waste a CI run on obviously-broken code.
        Authoritative correctness is then decided by REAL GitHub Actions CI:
        every retry cycle is driven by actual build/test output, never the
        validator's simulated review.
        """
        ticket_key, state, config = ctx.ticket_key, ctx.state, ctx.config
        ci_max_attempts = config.ci_max_attempts

        # ── Phase 1: Generate + cheap pre-flight validator filter ─────────
        state.stage = "implementing"
        impl_outputs, test_outputs = await self._generate_files(ctx, validator_feedback=[])

        state.stage = "preflight"
        validation = await self._validate(ctx, impl_outputs, test_outputs)
        if not validation.get("passed"):
            feedback = validation.get("issues", [])
            logger.info("preflight_filter_failed", ticket=ticket_key, issues=feedback)
            # ONE corrective pass — not a retry loop. CI is the real gate, so
            # don't burn cycles here; just clear the obvious stuff cheaply.
            impl_outputs, test_outputs = await self._generate_files(
                ctx, validator_feedback=feedback
            )
        logger.info("preflight_complete", ticket=ticket_key, passed=validation.get("passed"))

        # ── Phase 2: Mint, commit, open draft PR ──────────────────────────
        files_changed = [o["path"] for o in impl_outputs + test_outputs]
        minted = await self._mint(ctx, files_changed, validation.get("summary", ""))

        state.stage = "committing"
        await self._github.commit_changes(
            branch=state.branch,
            files=self._build_file_changes(ctx, impl_outputs, test_outputs),
            message=minted["commit_message"],
        )

        state.stage = "opening_pr"
        title = (
            f"[WIP] {ctx.spec.get('title') or minted['pr_title']}"
            if config.draft_prs
            else minted["pr_title"]
        )
        pr = await self._github.open_pull_request(
            branch=state.branch,
            title=title,
            body=minted["pr_body"],
            base_branch=self._settings.github_base_branch,
            draft=config.draft_prs,
        )
        state.pr_url = pr.url
        state.pr_number = pr.number
        await add_pipeline_comment(
            self._jira, ticket_key,
            f"🤖 Draft PR opened (#{pr.number}) — running real CI before marking it "
            f"ready for review.\nPR: {pr.url}"
        )

        # ── Phase 3: Real CI loop = the gate ──────────────────────────────
        state.stage = "waiting_for_ci"
        ci_status = await self._github.poll_pr_until_complete(pr.number)
        state.ci_state = ci_status.state

        attempt = 0
        while ci_status.state == "failure" and attempt < ci_max_attempts:
            attempt += 1
            ci_logs = await self._github.get_failed_check_logs(pr.number)
            logger.warning(
                "ci_gate_retry", ticket=ticket_key, attempt=attempt, pr=pr.number,
                failed=ci_status.failed,
            )
            await add_pipeline_comment(
                self._jira, ticket_key,
                f"🔄 CI attempt {attempt}/{ci_max_attempts} failed on PR #{pr.number} — "
                f"fixing from real CI output.\nFailed checks: {', '.join(ci_status.failed)}"
            )
            ci_feedback = [f"CI failed with the following errors — fix ALL of them:\n{ci_logs}"]

            state.stage = "implementing"
            impl_outputs, test_outputs = await self._generate_files(
                ctx, validator_feedback=ci_feedback
            )
            state.stage = "committing"
            await self._github.commit_changes(
                branch=state.branch,
                files=self._build_file_changes(ctx, impl_outputs, test_outputs),
                message=f"fix: address CI failures (attempt {attempt})\n\n{minted['commit_message']}",
            )
            logger.info("ci_fix_committed", branch=state.branch, attempt=attempt)

            state.stage = "waiting_for_ci"
            ci_status = await self._github.poll_pr_until_complete(pr.number)
            state.ci_state = ci_status.state

        # ── Phase 4: Finalize ─────────────────────────────────────────────
        if ci_status.state == "failure":
            await add_pipeline_comment(
                self._jira, ticket_key,
                f"❌ CI still failing after {ci_max_attempts} attempts on PR #{pr.number}. "
                f"Left as a draft for manual review.\nPR: {pr.url}"
            )
            state.status = "failed"
            state.error = f"CI failed after {ci_max_attempts} attempts: {ci_status.failed}"
            return
        if ci_status.state == "error":
            await add_pipeline_comment(
                self._jira, ticket_key,
                f"⚠️ CI did not complete in time on PR #{pr.number}. "
                f"Left as a draft for manual review.\nPR: {pr.url}"
            )
            state.status = "failed"
            state.error = "CI polling timed out"
            return
        if ci_status.state == "none":
            # Repo has no PR CI — the pre-flight validator was the only gate.
            logger.warning(
                "ci_gate_no_checks", ticket=ticket_key, pr=pr.number,
                hint="repo has no PR CI; relied on pre-flight validator only",
            )
            await add_pipeline_comment(
                self._jira, ticket_key,
                f"ℹ️ No CI checks ran on PR #{pr.number}; relied on pre-flight review only.\n"
                f"PR: {pr.url}"
            )

        await self._finalize_pr(ctx, pr, minted)

    async def _run_validator_gate_flow(self, ctx: _GateContext) -> None:
        """Legacy flow: the LLM validator is the gate (no real CI in the loop).

        Kept as an escape hatch via `ci_gate=False` for repos with no PR CI or
        CI too slow to gate on. Implement → validate retries up to max_retries,
        then open the PR and best-effort retry once against real CI output.
        """
        ticket_key, state, config = ctx.ticket_key, ctx.state, ctx.config
        max_retries = config.max_retries_per_stage

        validation: dict = {}
        impl_outputs: list = []
        test_outputs: list = []
        validator_feedback: list[str] = []

        for attempt in range(1, max_retries + 1):
            state.stage = "implementing"
            logger.info("implementation_attempt", ticket=ticket_key, attempt=attempt, max=max_retries)
            impl_outputs, test_outputs = await self._generate_files(
                ctx, validator_feedback=validator_feedback
            )
            logger.info(
                "stage_complete", stage="implementing", ticket=ticket_key,
                files=len(impl_outputs) + len(test_outputs), attempt=attempt,
            )

            state.stage = "validator"
            validation = await self._validate(ctx, impl_outputs, test_outputs)
            if validation.get("passed"):
                logger.info("stage_complete", stage="validator", ticket=ticket_key, attempt=attempt)
                break

            validator_feedback = validation.get("issues", [])
            logger.warning(
                "validation_failed_retrying", ticket=ticket_key, attempt=attempt,
                issues=validator_feedback,
            )
            issues_text = "\n".join(f"- {i}" for i in validator_feedback)
            await add_pipeline_comment(
                self._jira, ticket_key,
                f"🔄 Validation attempt {attempt}/{max_retries} failed — retrying with feedback:\n{issues_text}"
            )

        if not validation.get("passed"):
            issues = validation.get("issues", [])
            raise _HaltError(f"Validation failed after {max_retries} attempts: {'; '.join(issues)}")

        files_changed = [o["path"] for o in impl_outputs + test_outputs]
        minted = await self._mint(ctx, files_changed, validation.get("summary", ""))

        state.stage = "committing"
        await self._github.commit_changes(
            branch=state.branch,
            files=self._build_file_changes(ctx, impl_outputs, test_outputs),
            message=minted["commit_message"],
        )
        logger.info("committed", branch=state.branch)

        state.stage = "opening_pr"
        pr = await self._github.open_pull_request(
            branch=state.branch,
            title=minted["pr_title"],
            body=minted["pr_body"],
            base_branch=self._settings.github_base_branch,
        )
        state.pr_url = pr.url
        state.pr_number = pr.number

        ok = await transition_ticket(self._jira, ticket_key, "In Code Review")
        logger.info("jira_transition", ticket=ticket_key, status="In Code Review", ok=ok)
        await add_pipeline_comment(self._jira, ticket_key, minted["jira_comment"])

        state.stage = "waiting_for_ci"
        ci_status = await self._github.poll_pr_until_complete(pr.number)
        state.ci_state = ci_status.state

        if ci_status.state == "failure":
            logger.warning("ci_failed", pr=pr.number, failed=ci_status.failed, ticket=ticket_key)
            ci_logs = await self._github.get_failed_check_logs(pr.number)
            await add_pipeline_comment(
                self._jira, ticket_key,
                f"CI failed on PR #{pr.number} — retrying implementation with failure details.\n"
                f"Failed checks: {', '.join(ci_status.failed)}\nPR: {pr.url}"
            )
            ci_feedback = [f"CI failed with the following errors — fix ALL of them:\n{ci_logs}"]

            for attempt in range(1, max_retries + 1):
                state.stage = "implementing"
                logger.info("ci_retry_attempt", ticket=ticket_key, attempt=attempt)
                impl_outputs, test_outputs = await self._generate_files(
                    ctx, validator_feedback=ci_feedback
                )
                state.stage = "validator"
                validation = await self._validate(ctx, impl_outputs, test_outputs)
                if validation.get("passed"):
                    logger.info("ci_retry_validation_passed", attempt=attempt)
                    break
                ci_feedback = [
                    f"CI failed with the following errors — fix ALL of them:\n{ci_logs}"
                ] + validation.get("issues", [])

            if not validation.get("passed"):
                state.status = "failed"
                state.error = f"CI checks failed and retry validation did not pass: {ci_status.failed}"
                return

            state.stage = "committing"
            await self._github.commit_changes(
                branch=state.branch,
                files=self._build_file_changes(ctx, impl_outputs, test_outputs),
                message=f"fix: address CI failures\n\n{minted['commit_message']}",
            )
            logger.info("ci_fix_committed", branch=state.branch)

            state.stage = "waiting_for_ci"
            ci_status = await self._github.poll_pr_until_complete(pr.number)
            state.ci_state = ci_status.state

            if ci_status.state == "failure":
                await add_pipeline_comment(
                    self._jira, ticket_key,
                    f"CI still failing after retry on PR #{pr.number}. "
                    f"Manual review required.\nPR: {pr.url}"
                )
                state.status = "failed"
                state.error = f"CI still failing after retry: {ci_status.failed}"
                return

        state.status = "done"
        state.stage = "done"
        logger.info("pipeline_complete", ticket=ticket_key, pr=pr.url, ci=ci_status.state)

    async def _finalize_pr(
        self, ctx: _GateContext, pr: Any, minted: dict[str, Any]
    ) -> None:
        """Swap provisional draft text for the final minted PR, mark ready for
        review, and transition the ticket to In Code Review."""
        ticket_key, state, config = ctx.ticket_key, ctx.state, ctx.config
        if config.draft_prs:
            state.stage = "finalizing_pr"
            await self._github.update_pull_request(
                pr.number, title=minted["pr_title"], body=minted["pr_body"]
            )
            try:
                await self._github.mark_pr_ready(pr.node_id)
            except Exception as e:
                # Non-fatal: the PR exists and CI is green; a human can click
                # "Ready for review" if the mutation failed.
                logger.warning("mark_pr_ready_failed", ticket=ticket_key, error=str(e))

        ok = await transition_ticket(self._jira, ticket_key, "In Code Review")
        logger.info("jira_transition", ticket=ticket_key, status="In Code Review", ok=ok)
        await add_pipeline_comment(self._jira, ticket_key, minted["jira_comment"])

        state.status = "done"
        state.stage = "done"
        logger.info("pipeline_complete", ticket=ticket_key, pr=pr.url, ci=state.ci_state)

    # ------------------------------------------------------------------
    # Stage helpers (shared by both gate flows)
    # ------------------------------------------------------------------

    async def _generate_files(
        self, ctx: _GateContext, validator_feedback: list[str]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Run implementers (parallel) then test writers (parallel, seeing the
        fresh implementer output). Returns (impl_outputs, test_outputs)."""
        max_retries = ctx.config.max_retries_per_stage
        impl_tasks = [
            self._run_implementer(
                f, ctx.plan, ctx.spec, ctx.existing_contents, ctx.config, max_retries,
                validator_feedback=validator_feedback,
            )
            for f in ctx.impl_files
        ]
        impl_outputs = list(await asyncio.gather(*impl_tasks))

        impl_content_map = {
            **ctx.existing_contents,
            **{o["path"]: o["content"] for o in impl_outputs},
        }
        test_tasks = [
            self._run_test_writer(
                t, ctx.spec, impl_content_map, ctx.config, max_retries,
                validator_feedback=validator_feedback,
            )
            for t in ctx.test_file_specs
        ] if ctx.config.write_tests else []
        test_outputs = list(await asyncio.gather(*test_tasks)) if test_tasks else []
        return impl_outputs, test_outputs

    async def _validate(
        self,
        ctx: _GateContext,
        impl_outputs: list[dict[str, Any]],
        test_outputs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        validator_input: dict[str, Any] = {
            "spec": ctx.spec,
            "implementation_files": {o["path"]: o["content"] for o in impl_outputs},
            "test_files": {o["path"]: o["content"] for o in test_outputs},
            "coding_standards": ctx.config.coding_standards,
        }
        if ctx.past_review_signals:
            validator_input["past_review_signals"] = ctx.past_review_signals
        return await _with_retry(
            lambda: self._runner.run("validator", validator_input),
            ctx.config.max_retries_per_stage,
            "validator",
        )

    async def _mint(
        self, ctx: _GateContext, files_changed: list[str], validator_summary: str
    ) -> dict[str, Any]:
        minted = await _with_retry(
            lambda: self._runner.run("pr_minter", {
                "spec": ctx.spec,
                "plan": ctx.plan,
                "files_changed": files_changed,
                "validator_summary": validator_summary,
                "ticket_key": ctx.ticket_key,
            }),
            ctx.config.max_retries_per_stage,
            "pr_minter",
        )
        logger.info("stage_complete", stage="pr_minter", ticket=ctx.ticket_key)
        return minted

    @staticmethod
    def _build_file_changes(
        ctx: _GateContext,
        impl_outputs: list[dict[str, Any]],
        test_outputs: list[dict[str, Any]],
    ) -> list[FileChange]:
        return [
            FileChange(
                path=o["path"],
                content=o["content"],
                action=next(
                    (f.get("action", "modify") for f in ctx.impl_files if f["path"] == o["path"]),
                    "modify",
                ),
            )
            for o in impl_outputs + test_outputs
        ]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _make_branch(self, base_name: str) -> str:
        """Create a branch, appending a datestamp if the base name already exists."""
        try:
            return await self._github.create_branch(
                base_name, from_branch=self._settings.github_base_branch
            )
        except Exception:
            from datetime import date
            stamped = f"{base_name}-{date.today().strftime('%Y%m%d')}"
            return await self._github.create_branch(
                stamped, from_branch=self._settings.github_base_branch
            )

    async def _run_implementer(
        self,
        file_spec: dict[str, Any],
        plan: dict[str, Any],
        spec: dict[str, Any],
        existing_contents: dict[str, str],
        config: RepoConfig,
        max_retries: int,
        validator_feedback: list[str] | None = None,
    ) -> dict[str, Any]:
        path = file_spec["path"]
        related = {k: v for k, v in existing_contents.items() if k != path}

        history_query = (
            f"{file_spec.get('reason', '')} {plan.get('approach', '')} {path}"
        ).strip()
        historical_examples = await self._fetch_history(
            query_text=history_query,
            limit=3,
            file_path=path,
            hybrid=config.code_history_hybrid,
            diff_chars_per_hit=config.code_history_diff_chars_per_hit,
        )

        input_data: dict[str, Any] = {
            "path": path,
            "action": file_spec.get("action", "modify"),
            "reason": file_spec.get("reason", ""),
            "plan_approach": plan.get("approach", ""),
            "spec": spec,
            "existing_content": existing_contents.get(path, ""),
            "related_files": related,
            "coding_standards": config.coding_standards,
        }
        if validator_feedback:
            input_data["validator_feedback"] = validator_feedback
        if historical_examples:
            input_data["historical_examples"] = historical_examples

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
        validator_feedback: list[str] | None = None,
    ) -> dict[str, Any]:
        path = test_spec["path"]
        input_data = {
            "path": path,
            "covers": test_spec.get("covers", []),
            "spec": spec,
            "test_framework": config.test_framework,
            "implementation_contents": {
                k: v for k, v in existing_contents.items()
                if not k.startswith("tests/")
            },
            "existing_test_content": existing_contents.get(path, ""),
            "coding_standards": config.coding_standards,
        }
        if validator_feedback:
            input_data["validator_feedback"] = validator_feedback

        return await _with_retry(
            lambda: self._runner.run("test_writer", input_data), max_retries, f"test_writer:{path}"
        )

    async def _fetch_history(
        self,
        query_text: str,
        *,
        limit: int = 5,
        file_path: str | None = None,
        hybrid: bool = False,
        diff_chars_per_hit: int = 3000,
    ) -> list[dict[str, Any]]:
        """Vector-search the code-history store for relevant past PRs.

        Returns a small dict per hit with the fields the agents actually need.
        No-op (returns []) when code_history is not configured, so the pipeline
        runs identically with or without long-term memory enabled.

        hybrid: when True, fetch the actual patch from GitHub for each hit
                and attach as `diff`. Costs one GH API call per hit. When
                file_path is also set, the patch is narrowed to that file.
                Useful for the implementer (file-scoped) and validator
                (spec-scoped) so they ground generation in real code, not
                Haiku summaries.
        diff_chars_per_hit: hard cap on the diff size for each hit. Truncation
                            is marked inline so the model knows it's partial.
        """
        if not self._code_history:
            return []
        if not query_text.strip():
            return []
        try:
            hits = await self._code_history.search_similar(
                query_text=query_text,
                limit=limit,
                kind="commit",
                file_path=file_path,
            )
        except Exception as e:
            logger.warning("code_history_search_failed", error=str(e))
            return []

        results: list[dict[str, Any]] = []
        for h in hits:
            entry: dict[str, Any] = {
                "summary": h.get("text", ""),
                "title": h.get("title", ""),
                "pr_number": h.get("pr_number", 0),
                "files": h.get("files", []),
                "ticket_key": h.get("ticket_key", ""),
            }
            if hybrid and entry["pr_number"]:
                # Best-effort diff fetch — silently skip on error so the hit's
                # summary is still useful even when the GitHub call fails.
                entry["diff"] = await self._github.get_pr_diff(
                    entry["pr_number"],
                    file_filter=file_path,
                    max_chars=diff_chars_per_hit,
                )
            results.append(entry)
        return results

    async def _fetch_backlog_examples(
        self, exclude_key: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        label = self._settings.jira_processed_label
        jql = (
            f'project = "{self._settings.jira_project_key}" '
            f'AND labels = "{label}" '
            f'AND key != "{exclude_key}" '
            f"ORDER BY created DESC"
        )
        try:
            return await self._jira.search_ticket_examples(jql, limit=limit)
        except Exception:
            logger.warning("backlog_examples_fetch_failed", exclude_key=exclude_key)
            return []

    async def _fetch_formatter_configs(self, branch: str) -> str:
        """Fetch Prettier/ESLint/EditorConfig files from the repo root and return
        them as a formatted string to append to coding_standards."""
        config_files = [
            ".prettierrc",
            ".prettierrc.json",
            ".prettierrc.js",
            ".prettierrc.yaml",
            ".prettierrc.yml",
            "prettier.config.js",
            ".eslintrc",
            ".eslintrc.json",
            ".eslintrc.js",
            ".editorconfig",
        ]
        contents = await self._fetch_file_contents(config_files, branch)
        if not contents:
            return ""
        sections = [
            f"=== {name} ===\n{content}" for name, content in contents.items()
        ]
        return "\n\n".join(sections)

    async def _fetch_relevant_files(
        self, affected_areas: list[str], all_files: list[str]
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
        return await self._fetch_file_contents(relevant, self._settings.github_base_branch)

    async def _fetch_file_contents(
        self, paths: list[str], branch: str
    ) -> dict[str, str]:
        """Fetch multiple files concurrently. Skips files that don't exist."""
        async def _get(path: str) -> tuple[str, str]:
            try:
                content = await self._github.get_file(path, branch)
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


def _format_plan_comment(
    ticket_key: str, spec: dict[str, Any], plan: dict[str, Any]
) -> str:
    files = plan.get("files_to_modify", [])
    file_lines = "\n".join(
        f"- `{f['path']}` ({f['action']}): {f['reason']}" for f in files
    )
    risks = "\n".join(f"- {r}" for r in plan.get("risks", []))
    return (
        f"🤖 **Autonomous pipeline — plan ready for approval** ({ticket_key})\n\n"
        f"**Approach:** {plan.get('approach', '')}\n\n"
        f"**Files to change:**\n{file_lines}\n\n"
        f"**Risks:**\n{risks or '(none identified)'}\n\n"
        "To proceed with implementation, call `process_ticket` with "
        f"`ticket_key={ticket_key!r}` and `approve_plan=True`."
    )
