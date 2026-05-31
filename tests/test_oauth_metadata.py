"""Tests for the claude.ai / mobile OAuth connector authorization-server metadata."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from giga_mcp_server import server
from giga_mcp_server.config import Settings
from giga_mcp_server.server import _oauth_metadata, oauth_authorization_server_metadata


def _oauth_settings(**overrides) -> Settings:
    base = dict(
        oauth_connector_enabled=True,
        public_url="https://mcp.punch.gigacorp.co",
        cognito_hosted_ui_domain="https://giga-mcp-punch-pwa.auth.us-east-1.amazoncognito.com",
        cognito_region="us-east-1",
        cognito_user_pool_id="us-east-1_TestPool",
        cognito_oauth_scopes="openid profile email",
    )
    base.update(overrides)
    return Settings(**base)


class TestOAuthMetadataDoc:
    def test_advertises_s256_and_hosted_ui_endpoints(self):
        md = _oauth_metadata(_oauth_settings())
        assert md["issuer"] == "https://mcp.punch.gigacorp.co"
        assert (
            md["authorization_endpoint"]
            == "https://giga-mcp-punch-pwa.auth.us-east-1.amazoncognito.com/oauth2/authorize"
        )
        assert (
            md["token_endpoint"]
            == "https://giga-mcp-punch-pwa.auth.us-east-1.amazoncognito.com/oauth2/token"
        )
        assert md["code_challenge_methods_supported"] == ["S256"]
        assert md["scopes_supported"] == ["openid", "profile", "email"]

    def test_omits_registration_endpoint(self):
        # Cognito has no Dynamic Client Registration; claude.ai uses a manually
        # entered client_id, so advertising a registration_endpoint would mislead it.
        assert "registration_endpoint" not in _oauth_metadata(_oauth_settings())

    def test_jwks_points_at_cognito(self):
        md = _oauth_metadata(_oauth_settings())
        assert (
            md["jwks_uri"]
            == "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TestPool/.well-known/jwks.json"
        )

    def test_trailing_slash_on_hosted_ui_is_normalised(self):
        md = _oauth_metadata(
            _oauth_settings(cognito_hosted_ui_domain="https://x.auth.us-east-1.amazoncognito.com/")
        )
        assert md["authorization_endpoint"] == "https://x.auth.us-east-1.amazoncognito.com/oauth2/authorize"


@pytest.mark.asyncio
class TestOAuthMetadataRoute:
    async def _call(self, settings: Settings, method: str = "GET"):
        # The route reads module globals (like the webhook route); no MCP request context.
        server._runtime = None
        original = server._settings
        server._settings = settings
        try:
            request = MagicMock()
            request.method = method
            return await oauth_authorization_server_metadata(request)
        finally:
            server._settings = original

    async def test_returns_metadata_when_enabled(self):
        resp = await self._call(_oauth_settings())
        assert resp.status_code == 200

    async def test_404_when_disabled(self):
        resp = await self._call(_oauth_settings(oauth_connector_enabled=False))
        assert resp.status_code == 404

    async def test_options_preflight_returns_204(self):
        resp = await self._call(_oauth_settings(), method="OPTIONS")
        assert resp.status_code == 204
