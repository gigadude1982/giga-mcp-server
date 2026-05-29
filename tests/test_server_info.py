"""get_server_info should surface the per-stage models + feature flags."""

import asyncio
from types import SimpleNamespace

from giga_mcp_server.config import Settings
from giga_mcp_server.server import get_server_info


def _ctx(**overrides):
    # explicit kwargs win over any .env/defaults, so the asserted fields are deterministic
    s = Settings(server_name="test-mcp", **overrides)
    return SimpleNamespace(
        request_context=SimpleNamespace(lifespan_context=SimpleNamespace(settings=s))
    )


def test_reports_per_stage_pipeline_models():
    out = asyncio.run(get_server_info(_ctx()))
    assert "test-mcp" in out
    assert "Pipeline (per stage)" in out
    assert "implementer: `claude-opus-4-8`" in out
    assert "digester: `claude-sonnet-4-6`" in out
    assert "pr_minter: `claude-haiku-4-5-20251001`" in out


def test_reports_feature_flags():
    out = asyncio.run(get_server_info(_ctx(
        vector_enabled=True, pinecone_index_name="x-tickets",
        codehistory_enabled=True, pinecone_codehistory_index_name="x-ch",
        cognito_user_pool_id="",
    )))
    assert "Ticket store: enabled · index `x-tickets`" in out
    assert "Code-history store: enabled · index `x-ch`" in out
    assert "Auth (Cognito): disabled" in out


def test_auth_enabled_shows_pool():
    out = asyncio.run(get_server_info(_ctx(cognito_user_pool_id="us-east-1_ABC")))
    assert "Auth (Cognito): enabled · pool `us-east-1_ABC`" in out
