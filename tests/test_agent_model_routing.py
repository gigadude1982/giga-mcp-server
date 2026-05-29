"""Tests for per-stage model routing and the override-reset contract.

Regression guard: the orchestrator is a long-lived shared instance, so a repo's
`pipeline_model` (set as `model_override`) must be CLEARED between runs — else it
leaks into the next ticket and forces every stage onto the wrong model.
"""

from giga_mcp_server.pipeline.agent_runner import AgentRunner


def _runner() -> AgentRunner:
    # __init__ only stores the api_key in an AsyncAnthropic client; no network.
    return AgentRunner(api_key="test-key")


def test_per_stage_models_used_by_default():
    r = _runner()
    assert r.resolve_model("implementer") == "claude-opus-4-8"
    assert r.resolve_model("digester") == "claude-sonnet-4-6"
    assert r.resolve_model("pr_minter") == "claude-haiku-4-5-20251001"


def test_override_forces_all_stages():
    r = _runner()
    r.model_override = "claude-opus-4-8"
    assert r.resolve_model("digester") == "claude-opus-4-8"
    assert r.resolve_model("pr_minter") == "claude-opus-4-8"


def test_clearing_override_restores_per_stage_routing():
    # Simulates ticket A (override set) followed by ticket B (no pipeline_model,
    # so the orchestrator assigns None). The override MUST not leak into B.
    r = _runner()
    r.model_override = "claude-opus-4-8"  # ticket A's repo pipeline_model
    r.model_override = None  # ticket B: orchestrator assigns config.pipeline_model (None)
    assert r.resolve_model("pr_minter") == "claude-haiku-4-5-20251001"
    assert r.resolve_model("digester") == "claude-sonnet-4-6"


def test_unknown_agent_falls_back_to_default():
    r = _runner()
    assert r.resolve_model("does_not_exist") == r.model
