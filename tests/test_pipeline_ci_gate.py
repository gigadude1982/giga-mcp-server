"""Tests for the CI-as-gate orchestrator flow.

These exercise the gate flows directly (`_run_ci_gate_flow` /
`_run_validator_gate_flow`) with the GitHub client and agent runner mocked at
the boundary, so no network or LLM calls happen. The key behaviours under test:

* real CI is the authoritative gate (not the LLM validator),
* the validator runs only as a one-shot pre-flight filter,
* draft PRs are opened and marked ready only once CI is green,
* a repo with no PR CI ("none") still finalizes off the pre-flight verdict,
* exhausting CI retries fails the run and leaves the PR as a draft.
"""

from __future__ import annotations

import collections
from unittest.mock import AsyncMock, MagicMock

import pytest

from giga_mcp_server.pipeline import orchestrator as orch_mod
from giga_mcp_server.pipeline.github_tools import ChecksStatus, PullRequest
from giga_mcp_server.pipeline.orchestrator import (
    PipelineOrchestrator,
    PipelineState,
    _GateContext,
)
from giga_mcp_server.pipeline.repo_config import RepoConfig


class FakeRunner:
    """Stand-in for AgentRunner. Returns canned per-stage output and records
    how many times each stage was invoked."""

    def __init__(self, validator_results: list[bool] | None = None) -> None:
        # Consumed one-per-validator-call; defaults to passing when exhausted.
        self._validator_results = list(validator_results or [])
        self.calls: collections.Counter = collections.Counter()
        self.model_override = None

    async def run(self, stage: str, input_data: dict) -> dict:
        self.calls[stage] += 1
        if stage == "implementer":
            return {"path": input_data["path"], "content": "// code", "explanation": "e"}
        if stage == "test_writer":
            return {"path": input_data["path"], "content": "// test", "test_count": 1, "covers": []}
        if stage == "validator":
            passed = self._validator_results.pop(0) if self._validator_results else True
            return {
                "passed": passed,
                "issues": [] if passed else ["fix the thing"],
                "warnings": [],
                "summary": "looks fine",
            }
        if stage == "pr_minter":
            return {
                "pr_title": "feat: do the thing",
                "pr_body": "## Summary\n- did it",
                "jira_comment": "done",
                "commit_message": "feat: do the thing",
            }
        raise AssertionError(f"unexpected stage {stage!r}")


def _make_github(poll_states: list[str], draft: bool = True) -> AsyncMock:
    gh = AsyncMock()
    gh.open_pull_request.return_value = PullRequest(
        number=7, url="https://gh/pr/7", branch="auto/pit-1", node_id="PR_node", draft=draft
    )
    gh.poll_pr_until_complete.side_effect = [
        ChecksStatus(
            state=s,
            failed=["build"] if s == "failure" else [],
            passed=["build"] if s == "success" else [],
        )
        for s in poll_states
    ]
    gh.get_failed_check_logs.return_value = "ERROR: it broke"
    return gh


def _make_orch(runner: FakeRunner, github: AsyncMock) -> PipelineOrchestrator:
    settings = MagicMock()
    settings.anthropic_api_key = "k"
    settings.github_token = "t"
    settings.github_repo = "o/r"
    settings.pipeline_commit_author_name = "bot"
    settings.pipeline_commit_author_email = "bot@e"
    settings.github_base_branch = "main"
    settings.pipeline_max_retries = 3
    orch = PipelineOrchestrator(settings, MagicMock(), code_history=None)
    orch._runner = runner  # type: ignore[assignment]
    orch._github = github  # type: ignore[assignment]
    return orch


def _make_ctx(config: RepoConfig) -> _GateContext:
    return _GateContext(
        ticket_key="PIT-1",
        state=PipelineState(ticket_key="PIT-1", branch="auto/pit-1"),
        config=config,
        spec={"title": "Do the thing", "summary": "a thing"},
        plan={
            "approach": "approach",
            "files_to_modify": [{"path": "src/a.js", "action": "modify", "reason": "r"}],
            "test_files": [{"path": "src/a.test.js", "covers": []}],
        },
        impl_files=[{"path": "src/a.js", "action": "modify", "reason": "r"}],
        test_file_specs=[{"path": "src/a.test.js", "covers": []}],
        existing_contents={},
        past_review_signals=[],
    )


@pytest.fixture(autouse=True)
def _patch_jira(monkeypatch):
    monkeypatch.setattr(orch_mod, "add_pipeline_comment", AsyncMock())
    monkeypatch.setattr(orch_mod, "transition_ticket", AsyncMock(return_value=True))


@pytest.mark.asyncio
async def test_ci_gate_green_first_try():
    runner = FakeRunner(validator_results=[True])
    github = _make_github(["success"])
    orch = _make_orch(runner, github)
    ctx = _make_ctx(RepoConfig.defaults())

    await orch._run_ci_gate_flow(ctx)

    assert ctx.state.status == "done"
    assert ctx.state.ci_state == "success"
    # Opened as a draft, then marked ready once CI was green.
    assert github.open_pull_request.call_args.kwargs["draft"] is True
    github.mark_pr_ready.assert_awaited_once_with("PR_node")
    github.update_pull_request.assert_awaited_once()
    # One commit (no CI retries), one validator pass (the pre-flight filter).
    assert github.commit_changes.await_count == 1
    assert runner.calls["validator"] == 1


@pytest.mark.asyncio
async def test_ci_gate_fail_then_fix():
    runner = FakeRunner(validator_results=[True])
    github = _make_github(["failure", "success"])
    orch = _make_orch(runner, github)
    ctx = _make_ctx(RepoConfig.defaults())

    await orch._run_ci_gate_flow(ctx)

    assert ctx.state.status == "done"
    assert ctx.state.ci_state == "success"
    github.get_failed_check_logs.assert_awaited()  # real CI output fed back
    assert github.commit_changes.await_count == 2  # initial + one fix
    github.mark_pr_ready.assert_awaited_once()
    # Validator still ran only once — it is not in the retry loop.
    assert runner.calls["validator"] == 1


@pytest.mark.asyncio
async def test_ci_gate_exhausts_retries_and_fails():
    config = RepoConfig.defaults()
    config.ci_max_attempts = 2
    runner = FakeRunner(validator_results=[True])
    # initial poll + 2 retry polls, all failing
    github = _make_github(["failure", "failure", "failure"])
    orch = _make_orch(runner, github)
    ctx = _make_ctx(config)

    await orch._run_ci_gate_flow(ctx)

    assert ctx.state.status == "failed"
    assert "CI failed after 2 attempts" in ctx.state.error
    github.mark_pr_ready.assert_not_awaited()  # PR stays a draft
    # initial commit + 2 fix commits
    assert github.commit_changes.await_count == 3


@pytest.mark.asyncio
async def test_ci_gate_no_checks_falls_back_to_preflight():
    runner = FakeRunner(validator_results=[True])
    github = _make_github(["none"])
    orch = _make_orch(runner, github)
    ctx = _make_ctx(RepoConfig.defaults())

    await orch._run_ci_gate_flow(ctx)

    # No CI to gate on, but pre-flight passed → finalize anyway.
    assert ctx.state.status == "done"
    assert ctx.state.ci_state == "none"
    github.mark_pr_ready.assert_awaited_once()


@pytest.mark.asyncio
async def test_preflight_failure_triggers_one_corrective_pass():
    # Pre-flight validator fails once → exactly one corrective regeneration
    # before committing. With 1 impl file, that is 2 implementer calls total.
    runner = FakeRunner(validator_results=[False])
    github = _make_github(["success"])
    orch = _make_orch(runner, github)
    ctx = _make_ctx(RepoConfig.defaults())

    await orch._run_ci_gate_flow(ctx)

    assert ctx.state.status == "done"
    assert runner.calls["validator"] == 1  # filter runs once, no retry loop
    assert runner.calls["implementer"] == 2  # initial + one corrective pass
    assert github.commit_changes.await_count == 1


@pytest.mark.asyncio
async def test_validator_gate_flow_opens_non_draft_pr():
    runner = FakeRunner(validator_results=[True])
    github = _make_github(["success"])
    orch = _make_orch(runner, github)
    config = RepoConfig.defaults()
    config.ci_gate = False
    ctx = _make_ctx(config)

    await orch._run_validator_gate_flow(ctx)

    assert ctx.state.status == "done"
    # Legacy flow opens a normal (non-draft) PR and never marks ready.
    assert "draft" not in github.open_pull_request.call_args.kwargs
    github.mark_pr_ready.assert_not_awaited()
