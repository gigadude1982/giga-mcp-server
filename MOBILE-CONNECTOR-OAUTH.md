# Mobile / claude.ai connector OAuth (spike plan)

> **Status: NOT IMPLEMENTED. Desired before the demo if time allows.**
> Today a board's MCP server can only be reached from **Claude Desktop** (via
> `scripts/connect-claude-desktop.sh`, which bridges with `mcp-remote` + a static
> Cognito bearer token). The **Claude iPhone app and claude.ai web** cannot use
> that path — they connect through account-level **Connectors**, which speak
> OAuth 2.0 and have no field for a static bearer header.

## Why the desktop approach doesn't work on mobile

| | Claude Desktop | Claude mobile / claude.ai web |
| --- | --- | --- |
| Mechanism | local `npx mcp-remote` process bridges remote→stdio | account-level remote **Connector** |
| Auth | we inject a static `Authorization: Bearer <token>` header | OAuth 2.0 authorization-code flow only |
| Static token? | yes (`--header`) | **no** — no place to paste one |

A connector added in claude.ai shows up on web **and** phone (same account), so "connect from iPhone" == "register the server as an OAuth connector."

## What the server needs (resource server is already half-done)

The server is already an OAuth **resource server**: `server.py:_configure_auth` installs `CognitoTokenVerifier` and sets `AuthSettings(issuer_url=<cognito>, resource_server_url=<public_url>)`. Missing pieces for the full connector flow:

1. **Cognito hosted-UI domain** for the user pool (the login page the OAuth redirect lands on).
2. **App client OAuth config**: enable `authorization_code` grant + PKCE, set `AllowedOAuthFlows=[code]`, `AllowedOAuthScopes=[openid, profile, ...]`, and **callback URLs** for claude.ai's connector redirect.
3. **Discovery metadata**: confirm the MCP server serves `/.well-known/oauth-protected-resource` (MCP SDK may already, given `AuthSettings`) pointing at the Cognito authorization server, and that Cognito's `/.well-known/openid-configuration` is reachable.
4. **The DCR gap (main risk):** MCP connector clients generally expect **Dynamic Client Registration (RFC 7591)**; **Cognito does not support DCR**. Options:
   - (a) a thin **DCR shim** in front of Cognito: accepts the client's registration POST and returns a pre-provisioned Cognito app client's `client_id` (a small Lambda/route);
   - (b) check whether claude.ai's connector accepts a **pre-registered `client_id`** via metadata (no DCR needed) — try this first, it may be zero-code.

## CDK changes (when implementing)

Add to the `GigaMcpServerService` construct, behind an optional flag (e.g. `oauthConnectorEnabled`):
- `cognito.UserPoolDomain` (hosted UI),
- expand `addClient` with `oAuth: { flows: { authorizationCodeGrant: true }, scopes, callbackUrls }`,
- output the hosted-UI domain.
Keep it per-board so only the boards that need mobile access pay for it.

## Verification

In claude.ai → Settings → Connectors → add the board's MCP URL → it should trigger the Cognito hosted-UI login → after consent, the board's tools appear on **both** claude.ai web and the iPhone app.

## Effort / recommendation

Medium–high, with the DCR negotiation the principal unknown. **Spike it off the demo's critical path:** first test whether claude.ai connects to Cognito with a pre-registered client (option 4b, possibly no code). If that's blocked by DCR, build the shim (4a). The desktop connection already carries the demo if this isn't ready in time.
