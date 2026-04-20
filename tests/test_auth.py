"""Tests for CognitoTokenVerifier."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from giga_mcp_server.auth import CognitoTokenVerifier


@pytest.fixture
def rsa_keypair():
    """Generate an RSA keypair for signing test JWTs."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    return private_key, public_key


@pytest.fixture
def verifier():
    """Create a CognitoTokenVerifier with test config."""
    return CognitoTokenVerifier(
        user_pool_id="us-east-1_TestPool",
        region="us-east-1",
        client_id="test-client-id",
    )


def _make_token(private_key, claims: dict, headers: dict | None = None) -> str:
    """Create a signed JWT with the given claims."""
    return pyjwt.encode(claims, private_key, algorithm="RS256", headers=headers)


def _valid_claims(exp_offset: int = 3600) -> dict:
    """Return a valid Cognito access token claims set."""
    now = int(time.time())
    return {
        "sub": "user-123",
        "iss": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TestPool",
        "client_id": "test-client-id",
        "token_use": "access",
        "scope": "openid profile",
        "exp": now + exp_offset,
        "iat": now,
    }


@pytest.mark.asyncio
async def test_verify_valid_token(verifier, rsa_keypair):
    """Valid Cognito token returns AccessToken."""
    private_key, public_key = rsa_keypair
    token = _make_token(private_key, _valid_claims())

    mock_signing_key = MagicMock()
    mock_signing_key.key = public_key

    with patch.object(verifier._jwk_client, "get_signing_key_from_jwt", return_value=mock_signing_key):
        result = await verifier.verify_token(token)

    assert result is not None
    assert result.client_id == "test-client-id"
    assert result.scopes == ["openid", "profile"]


@pytest.mark.asyncio
async def test_verify_expired_token(verifier, rsa_keypair):
    """Expired token returns None."""
    private_key, public_key = rsa_keypair
    claims = _valid_claims(exp_offset=-3600)  # expired 1 hour ago
    token = _make_token(private_key, claims)

    mock_signing_key = MagicMock()
    mock_signing_key.key = public_key

    with patch.object(verifier._jwk_client, "get_signing_key_from_jwt", return_value=mock_signing_key):
        result = await verifier.verify_token(token)

    assert result is None


@pytest.mark.asyncio
async def test_verify_wrong_client_id(verifier, rsa_keypair):
    """Token with wrong client_id returns None."""
    private_key, public_key = rsa_keypair
    claims = _valid_claims()
    claims["client_id"] = "wrong-client"
    token = _make_token(private_key, claims)

    mock_signing_key = MagicMock()
    mock_signing_key.key = public_key

    with patch.object(verifier._jwk_client, "get_signing_key_from_jwt", return_value=mock_signing_key):
        result = await verifier.verify_token(token)

    assert result is None


@pytest.mark.asyncio
async def test_verify_wrong_token_use(verifier, rsa_keypair):
    """Token with token_use != 'access' returns None."""
    private_key, public_key = rsa_keypair
    claims = _valid_claims()
    claims["token_use"] = "id"
    token = _make_token(private_key, claims)

    mock_signing_key = MagicMock()
    mock_signing_key.key = public_key

    with patch.object(verifier._jwk_client, "get_signing_key_from_jwt", return_value=mock_signing_key):
        result = await verifier.verify_token(token)

    assert result is None


@pytest.mark.asyncio
async def test_verify_wrong_issuer(verifier, rsa_keypair):
    """Token with wrong issuer returns None."""
    private_key, public_key = rsa_keypair
    claims = _valid_claims()
    claims["iss"] = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_WRONG"
    token = _make_token(private_key, claims)

    mock_signing_key = MagicMock()
    mock_signing_key.key = public_key

    with patch.object(verifier._jwk_client, "get_signing_key_from_jwt", return_value=mock_signing_key):
        result = await verifier.verify_token(token)

    assert result is None


@pytest.mark.asyncio
async def test_verify_no_client_id_check_when_not_configured(rsa_keypair):
    """When client_id is not configured, any client_id is accepted."""
    verifier = CognitoTokenVerifier(
        user_pool_id="us-east-1_TestPool",
        region="us-east-1",
        client_id=None,
    )
    private_key, public_key = rsa_keypair
    claims = _valid_claims()
    claims["client_id"] = "any-client"
    token = _make_token(private_key, claims)

    mock_signing_key = MagicMock()
    mock_signing_key.key = public_key

    with patch.object(verifier._jwk_client, "get_signing_key_from_jwt", return_value=mock_signing_key):
        result = await verifier.verify_token(token)

    assert result is not None
    assert result.client_id == "any-client"


@pytest.mark.asyncio
async def test_verify_invalid_token(verifier):
    """Completely invalid token string returns None."""
    result = await verifier.verify_token("not.a.jwt")
    assert result is None


@pytest.mark.asyncio
async def test_verify_no_scopes(verifier, rsa_keypair):
    """Token without scope claim returns empty scopes list."""
    private_key, public_key = rsa_keypair
    claims = _valid_claims()
    del claims["scope"]
    token = _make_token(private_key, claims)

    mock_signing_key = MagicMock()
    mock_signing_key.key = public_key

    with patch.object(verifier._jwk_client, "get_signing_key_from_jwt", return_value=mock_signing_key):
        result = await verifier.verify_token(token)

    assert result is not None
    assert result.scopes == []
