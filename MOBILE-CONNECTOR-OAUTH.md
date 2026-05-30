# Mobile / claude.ai connector OAuth

> **Status: IMPLEMENTED (per-board, behind `oauthConnectorEnabled`).** Requires a
> `cdk deploy` + the manual claude.ai connector setup in
> [Enabling a board](#enabling-a-board-on-claudeai). Before this, a board's MCP server
> could only be reached from **Claude Desktop** (via `scripts/launch-claude-desktop.sh`,
> which bridges with `mcp-remote` + a static Cognito bearer token). The **Claude iPhone
> app and claude.ai web** cannot use that path — they connect through account-level
> **Connectors**, which speak OAuth 2.0 and have no field for a static bearer header.

## What shipped

A per-board flag `oauthConnectorEnabled` (`infra/config/boards.ts`, enabled on all three
boards) that, when true:

1. **Server** advertises *itself* as the OAuth authorization server. `_configure_auth`
   (`src/giga_mcp_server/server.py`) points the protected-resource metadata's
   `authorization_servers` at `GIGA_PUBLIC_URL`, and the server serves a hand-built
   **authorization-server metadata** doc at `/.well-known/oauth-authorization-server`
   **and** `/.well-known/openid-configuration` (`_oauth_metadata`). That doc points
   `authorization_endpoint`/`token_endpoint` at the Cognito **hosted UI**, advertises
   **S256 PKCE**, and **omits `registration_endpoint`** — Cognito has no Dynamic Client
   Registration, so claude.ai uses a manually-entered `client_id` instead. Token
   verification is unchanged (`CognitoTokenVerifier` still checks the Cognito `iss`), so
   the desktop bearer path is unaffected.
2. **CDK** (`infra/lib/constructs/giga-mcp-server-service.ts`) provisions a Cognito
   **hosted-UI domain** (`giga-mcp-<boardId>`), adds the **authorization-code + PKCE**
   grant to the app client with callback `https://claude.ai/api/mcp/auth_callback`
   (public client, no secret), and sets `GIGA_OAUTH_CONNECTOR_ENABLED` +
   `GIGA_COGNITO_HOSTED_UI_DOMAIN` on the App Runner service. Outputs
   `CognitoAppClientId`, `OAuthHostedUiDomain`, and `OAuthConnectorUrl`.

## Enabling a board on claude.ai

1. Merge to `main` → CI builds & pushes the image (App Runner pulls `:latest`).
2. `cd infra && npx cdk deploy --all` — creates the hosted-UI domains and updates app
   clients. (The domain prefix `giga-mcp-<boardId>` must be globally unique per region;
   change it if `cdk deploy` reports it taken.)
3. Verify discovery (example for punch-pwa):
   - `curl https://mcp.punch.gigacorp.co/.well-known/oauth-protected-resource`
     → `authorization_servers` lists `https://mcp.punch.gigacorp.co`.
   - `curl https://mcp.punch.gigacorp.co/.well-known/oauth-authorization-server`
     → hosted-UI authorize/token endpoints, `S256`, no `registration_endpoint`.
4. claude.ai → **Settings → Connectors → Add custom connector** → paste `OAuthConnectorUrl`
   → **Advanced settings** → paste **`CognitoAppClientId`** as the OAuth Client ID, leave
   the secret **blank** → Connect → Cognito hosted-UI login → consent.
5. The board's tools now appear on **claude.ai web and the iPhone app** (same account). File
   a ticket from the phone to confirm it reaches the backlog.

> **Note:** a Cognito user must exist in the board's pool to log in at the hosted UI — reuse
> the demo users created during pool setup, or add one via the console.

## Known fallbacks

- **issuer mismatch:** if claude.ai rejects the flow because the access token's `iss`
  (Cognito) differs from the metadata `issuer` (our URL), fall back to leaving
  `auth.issuer_url` = the Cognito issuer (don't override it) and let claude.ai read
  Cognito's own `openid-configuration`. The manual `client_id` still avoids DCR; we lose
  only control over the advertised PKCE/metadata.
- **connector URL path:** `OAuthConnectorUrl` uses the `/mcp` endpoint path. If claude.ai
  wants the base URL instead, adjust step 4.
- Cognito hosted-UI domain provisioning can take a few minutes before `/oauth2/authorize`
  is live.

---

## Background / design rationale

### Why the desktop approach doesn't work on mobile

| | Claude Desktop | Claude mobile / claude.ai web |
| --- | --- | --- |
| Mechanism | local `npx mcp-remote` process bridges remote→stdio | account-level remote **Connector** |
| Auth | we inject a static `Authorization: Bearer <token>` header | OAuth 2.0 authorization-code flow only |
| Static token? | yes (`--header`) | **no** — no place to paste one |

A connector added in claude.ai shows up on web **and** phone (same account), so "connect
from iPhone" == "register the server as an OAuth connector."

### Why the metadata override (and no DCR shim)

MCP connector clients generally expect **Dynamic Client Registration (RFC 7591)**, which
**Cognito does not support**. Two options were on the table: (a) a DCR shim that returns a
pre-provisioned Cognito `client_id`, or (b) rely on claude.ai's support for a manually
entered `client_id`. Research confirmed claude.ai added **manual OAuth Client ID + Secret**
fields under a connector's *Advanced settings* (July 2025), so **(b) needs no shim**.

The remaining gap was discovery metadata: Cognito serves only `openid-configuration`, and
its advertised metadata is known to trip claude.ai (missing `registration_endpoint` / PKCE
advertisement). The fix is to have the MCP server serve its own authorization-server
metadata pointing at Cognito's hosted-UI endpoints with `S256` declared and no
`registration_endpoint` — which is what shipped.
