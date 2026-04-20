"""Cognito JWT token verifier for MCP OAuth support."""

from __future__ import annotations

from typing import Any

import jwt
import structlog
from jwt import PyJWKClient
from mcp.server.auth.provider import AccessToken

logger = structlog.get_logger()


class CognitoTokenVerifier:
    """Verifies JWT access tokens issued by AWS Cognito.

    Implements the MCP SDK's TokenVerifier protocol.
    """

    def __init__(
        self,
        user_pool_id: str,
        region: str,
        client_id: str | None = None,
    ) -> None:
        self._user_pool_id = user_pool_id
        self._region = region
        self._client_id = client_id
        self._issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"
        self._jwks_url = f"{self._issuer}/.well-known/jwks.json"
        self._jwk_client = PyJWKClient(self._jwks_url, cache_keys=True)

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify a Cognito JWT and return AccessToken if valid."""
        try:
            signing_key = self._jwk_client.get_signing_key_from_jwt(token)

            claims: dict[str, Any] = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                issuer=self._issuer,
                options={
                    "verify_aud": False,  # Cognito access tokens don't have 'aud'
                    "verify_exp": True,
                },
            )

            # Cognito access tokens use 'client_id', not 'aud'
            token_client_id = claims.get("client_id", "")
            if self._client_id and token_client_id != self._client_id:
                logger.warning(
                    "token_client_id_mismatch",
                    expected=self._client_id,
                    got=token_client_id,
                )
                return None

            # Verify token_use is 'access'
            if claims.get("token_use") != "access":
                logger.warning("invalid_token_use", token_use=claims.get("token_use"))
                return None

            scopes = claims.get("scope", "").split() if claims.get("scope") else []

            return AccessToken(
                token=token,
                client_id=token_client_id,
                scopes=scopes,
                expires_at=claims.get("exp"),
            )

        except jwt.ExpiredSignatureError:
            logger.warning("token_expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.warning("token_invalid", error=str(e))
            return None
        except Exception:
            logger.exception("token_verification_error")
            return None
