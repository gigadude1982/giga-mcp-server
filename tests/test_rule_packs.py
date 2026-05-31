"""Tests for language-aware rule packs and their injection into agents."""

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
from giga_mcp_server.pipeline.rule_packs import (
    resolve_stack,
    role_rules,
    system_suffix,
)


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "language,expected",
    [
        ("python", "python"),
        ("javascript", "javascript-react"),
        ("typescript", "typescript-react"),
        ("TypeScript", "typescript-react"),  # case-insensitive
        ("ruby", "generic"),                 # unknown → agnostic fallback
        ("", "generic"),
        (None, "generic"),
    ],
)
def test_resolve_stack_from_language(language, expected):
    assert resolve_stack(language) == expected


def test_explicit_stack_overrides_language():
    # A non-React TS repo can pin "typescript" even though language guesses react.
    assert resolve_stack("typescript", stack="python") == "python"
    assert resolve_stack("javascript", stack="typescript-react") == "typescript-react"


def test_generic_stack_has_no_rules():
    for role in ("implementer", "test_writer", "validator"):
        assert role_rules("generic", role) == ""
        assert system_suffix("generic", role) == ""


def test_ts_pack_forbids_proptypes_js_pack_requires_them():
    ts_impl = role_rules("typescript-react", "implementer")
    js_impl = role_rules("javascript-react", "implementer")
    assert "Do NOT use PropTypes" in ts_impl
    assert "PropTypes" in js_impl and "MUST be declared in a PropTypes" in js_impl


def test_ts_test_pack_warns_about_global():
    # The exact gotcha that broke PUNCH-3: `global` in a .tsx test file.
    rules = role_rules("typescript-react", "test_writer")
    assert "globalThis" in rules
    assert "global" in rules


def test_system_suffix_is_labeled():
    suffix = system_suffix("typescript-react", "implementer")
    assert suffix.startswith("## Stack-specific rules (typescript-react)")


# --------------------------------------------------------------------------
# Injection into the agents via the orchestrator
# --------------------------------------------------------------------------

class _RecordingRunner:
    """Records the system_suffix passed to each agent call."""

    def __init__(self) -> None:
        self.suffix_by_agent: dict[str, str] = {}
        self.calls: collections.Counter = collections.Counter()
        self.model_override = None

    async def run(self, stage, input_data, system_suffix: str = ""):
        self.calls[stage] += 1
        self.suffix_by_agent[stage] = system_suffix
        if stage in ("implementer", "test_writer"):
            return {"path": input_data["path"], "content": "x", "explanation": "e",
                    "test_count": 1, "covers": []}
        if stage == "validator":
            return {"passed": True, "issues": [], "warnings": [], "summary": "ok"}
        if stage == "pr_minter":
            return {"pr_title": "t", "pr_body": "b", "jira_comment": "c", "commit_message": "m"}
        raise AssertionError(stage)


def _orch(runner, github):
    settings = MagicMock()
    settings.anthropic_api_key = "k"
    settings.github_token = "t"
    settings.github_repo = "o/r"
    settings.pipeline_commit_author_name = "bot"
    settings.pipeline_commit_author_email = "bot@e"
    settings.github_base_branch = "main"
    settings.pipeline_max_retries = 3
    o = PipelineOrchestrator(settings, MagicMock(), code_history=None)
    o._runner = runner  # type: ignore[assignment]
    o._github = github  # type: ignore[assignment]
    return o


def _ctx(config):
    return _GateContext(
        ticket_key="PIT-1",
        state=PipelineState(ticket_key="PIT-1", branch="auto/pit-1"),
        config=config,
        spec={"title": "T", "summary": "s"},
        plan={"approach": "a",
              "files_to_modify": [{"path": "src/a.tsx", "action": "modify", "reason": "r"}],
              "test_files": [{"path": "src/a.test.tsx", "covers": []}]},
        impl_files=[{"path": "src/a.tsx", "action": "modify", "reason": "r"}],
        test_file_specs=[{"path": "src/a.test.tsx", "covers": []}],
        existing_contents={},
        past_review_signals=[],
    )


@pytest.fixture(autouse=True)
def _patch_jira(monkeypatch):
    monkeypatch.setattr(orch_mod, "add_pipeline_comment", AsyncMock())
    monkeypatch.setattr(orch_mod, "transition_ticket", AsyncMock(return_value=True))


@pytest.mark.asyncio
async def test_typescript_repo_injects_ts_rules_into_each_agent():
    runner = _RecordingRunner()
    github = AsyncMock()
    github.open_pull_request.return_value = PullRequest(
        number=1, url="u", branch="b", node_id="n", draft=True
    )
    github.poll_pr_until_complete.return_value = ChecksStatus(state="success", passed=["ci"])
    orch = _orch(runner, github)
    config = RepoConfig.defaults()
    config.language = "typescript"

    await orch._run_ci_gate_flow(_ctx(config))

    for agent in ("implementer", "test_writer", "validator"):
        assert "typescript-react" in runner.suffix_by_agent[agent], agent
    # PR minter is stack-agnostic — no suffix.
    assert runner.suffix_by_agent["pr_minter"] == ""
