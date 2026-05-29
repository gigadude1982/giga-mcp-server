# Multi-Client Integration — design spike

Spike doc for exposing the deployed Bender MCP server (one per board) to multiple natural-language clients beyond Claude Desktop. The vision: a non-engineer types *"I want Punch to have a sleep button that restores energy"* into Teams / Slack / ChatGPT / Cursor, the MCP server creates the JIRA ticket and (optionally) auto-runs the pipeline end-to-end — PR drops in GitHub minutes later.

Companion to [`PLANE-SUPPORT.md`](./PLANE-SUPPORT.md) and [`BITBUCKET-SUPPORT.md`](./BITBUCKET-SUPPORT.md). Not committed to a timeline. Delete this file once the integrations ship or the idea is dropped.

Started 2026-05-28.

## What "supported" actually means per client

Two distinct integration shapes. Useful to disambiguate up front because they have very different effort profiles:

- **Native MCP client** — the platform speaks MCP directly. You give it the server URL + auth and tools appear. Effort: ~30 minutes per platform.
- **Bridge bot** — the platform doesn't speak MCP. You write a small bot/connector that calls the MCP server as an HTTP client and renders tool results in the platform's UX. Effort: 0.5–2 days per platform.

## Platform matrix

| Client                     | Native MCP?       | Transport            | Effort       | Notes                                                  |
| -------------------------- | ----------------- | -------------------- | ------------ | ------------------------------------------------------ |
| **Claude Desktop**         | ✅ Yes            | stdio                | done         | Already supported via `claude_desktop_config.json`     |
| **claude.ai (web)**        | ✅ Yes            | streamable-http      | done         | Already supported as a "custom connector"              |
| **ChatGPT (web/app)**      | ✅ Yes (since 2025) | streamable-http    | ~30 min      | OpenAI added MCP client support in Apr 2025; configure per workspace |
| **Cursor / Windsurf / Cline** | ✅ Yes         | stdio or http        | ~30 min      | MCP config in the editor's settings file               |
| **GitHub Copilot Chat**    | ✅ Yes (preview)  | http                 | ~30 min      | Limited rollout — verify availability before promising |
| **Microsoft Teams**        | ❌ Bridge needed  | bot → http           | ~1.5 days    | Easiest via Copilot Studio; full Bot Framework gives more control |
| **Slack**                  | ❌ Bridge needed  | bot → http           | ~1 day       | Bolt SDK; simpler than Teams                           |
| **Discord**                | ❌ Bridge needed  | bot → http           | ~1 day       | discord.py or discord.js                               |
| **iMessage / SMS**         | ❌ Bridge needed  | Twilio → http        | ~1.5 days    | Twilio webhook → Bender; needs auth design             |
| **Linear / Notion comments** | ❌ Bridge needed | webhook → http      | ~1 day each  | Useful for "comment `@bender PUNCH-42` to auto-process" |

## Native MCP integrations (~30 min each)

For platforms that speak MCP, the setup is essentially the same — paste the streamable-http URL of the deployed Bender service into the platform's MCP config.

**Example — ChatGPT (workspace MCP setup):**

1. Workspace admin opens ChatGPT settings → Connectors → Add MCP server
2. Paste `https://mcp.punch.gigacorp.co/mcp` (or whichever board)
3. Authenticate via the Cognito OAuth flow (if `enableAuth: true` in `boards.ts`)
4. Tools appear in the next chat session

**Example — Cursor (`.cursor/mcp.json` or settings):**

```json
{
  "mcpServers": {
    "punch-bender": {
      "url": "https://mcp.punch.gigacorp.co/mcp",
      "type": "http"
    }
  }
}
```

**Auth caveat:** Cognito JWT auth is enabled per-board via `enableAuth: true` in `boards.ts`. For the demo, leaving auth off (`enableAuth: false`) avoids the OAuth-bounce dance — fine for a single-engineer project, not fine for a team deployment. Pre-interview, decide: are you demoing locked-down auth, or open-tool access? If locked, walk through the Cognito setup once with each client. If open, mention auth is wired in (it is) but disabled for ease of demo.

## Bridge bot integrations

For platforms without native MCP, the pattern is the same across all of them:

```
User in Teams/Slack/Discord → Bot listens for message
                              → Bot maps message to MCP tool call
                              → Bot HTTP-POSTs to Bender MCP server
                              → Bot renders tool result back in the platform UX
```

A shared Python bridge library would cover ~80% of the work for any platform — only the platform's listener + render glue would differ. Worth building once if you ship more than two bridges.

### Microsoft Teams (~1.5 days)

**Best path: Microsoft Copilot Studio + Custom Connector**

1. Build a Copilot Studio agent that exposes Bender's tools as "skills."
2. Configure the agent's HTTP custom connector pointing at the deployed Bender URL.
3. Install the agent in a Teams channel.
4. Users `@mention` the agent to interact: *"@PunchBender add a story to feed Punch a banana"*

Pros: no Bot Framework code, no Azure-hosted bot, no manifest.json wrangling.
Cons: Copilot Studio licensing — needs an M365 Copilot plan or Power Platform license. Tied to the M365 ecosystem.

**Alternative path: Bot Framework + Azure Bot Service**

1. Write a Bot Framework bot (Node.js or Python) deployed to Azure App Service.
2. Bot registers with Bot Framework + Teams App Registration.
3. Bot's `onMessage` handler calls Bender via HTTP.

Pros: full control, no licensing dependency, can use Anthropic or OpenAI for the natural-language → tool-call mapping.
Cons: more code, Azure infra (yet another cloud), Teams app installation flow.

**Recommendation:** start with Copilot Studio for prototyping. Move to Bot Framework if licensing or control becomes load-bearing.

**Catches:**
- Teams messages don't have a thread model the same as Slack — replying to a bot is in the same channel. State tracking needs the bot to remember conversation context per user/channel.
- The pipeline runs for 5–15 minutes per ticket. Teams users will lose patience watching a typing indicator. Pattern: bot acknowledges immediately (*"Plan posted to JIRA: PUNCH-43. Watching for completion…"*) and follows up when the PR opens.

### Slack (~1 day)

**Path: Slack Bolt + Anthropic intent parser**

```python
from slack_bolt import App
import httpx, anthropic

app = App(token=SLACK_BOT_TOKEN)

@app.message()
async def handle(message, say):
    # 1. Ask Claude to map the message to a Bender tool call
    intent = await classify_intent(message["text"])
    # 2. Hit the deployed Bender server
    result = await call_bender_tool(intent.tool, intent.args)
    # 3. Render result back in Slack
    await say(format_for_slack(result))
```

Pros: cleanest API of the three (Slack, Teams, Discord). Threading model maps well to pipeline state ("here's the plan" + reply with "approve" maps to call → call).
Cons: same long-running-task issue as Teams.

**Catches:**
- Slack rate limits aren't generous when posting CI updates back. Batch the updates.
- The `app.message()` listener fires on every message in channels the bot is in. Scope it via `@mentions` or a slash command (`/bender add story for ...`).

### Discord (~1 day)

Same shape as Slack with `discord.py`. The community already has many "ask LLM" bot templates that can be adapted in a few hours by swapping the LLM call for a Bender MCP call.

**Catches:**
- Discord users expect *fast* responses. The "plan posted, watch JIRA for the PR" pattern still works but feels slower than Slack since Discord is more chat-realtime.

## Cross-cutting concerns

### Auth model per integration

| Integration               | Auth surface                                    | Recommended approach                          |
| ------------------------- | ----------------------------------------------- | --------------------------------------------- |
| Native MCP (Claude/ChatGPT/Cursor) | Cognito JWT via OAuth code flow         | Use `enableAuth: true` on the board; users authenticate per-client once |
| Teams / Slack / Discord bridge | Bot acts on behalf of the channel/user      | Bot holds a service-account API key; per-user attribution lives in JIRA ticket metadata |
| Webhook bridges (Linear etc.) | Signed webhook payloads                      | HMAC verification + service-account API key   |

The bridge bots **should not** have human-impersonation auth — they're shared service accounts. Per-user attribution travels in the JIRA `reporter` field set explicitly when the bot creates the ticket.

### Long-running pipeline UX

Pipeline runs take 5–15 minutes. None of the chat platforms have first-class support for "tool call that completes in 10 minutes." Pattern that works:

1. **Ack immediately** with a JIRA ticket key the user can watch: *"Created PUNCH-43, plan coming in ~30s."*
2. **Stream JIRA comments** back into the chat — the pipeline already posts to JIRA, so the bot can subscribe to JIRA webhooks and forward to chat.
3. **Final notification** when the PR opens: *"PR #57 opened for PUNCH-43: <link>. CI is running."*

For native MCP clients (Claude Desktop, ChatGPT), the tool call is async via the two-call `process_ticket` flow — first call returns immediately with `status: awaiting_approval`, second call resumes after the user approves. That UX already works without bridge code.

### Per-board client config

A single Bender deployment owns one board. To support multiple boards through the same client (e.g. an engineer wanting to talk to both `gigacorp-react` and `punch-pwa` from one Cursor session), the client needs to be configured with multiple MCP server URLs — one per board. There's no "Bender router" today, and probably shouldn't be — boards are the isolation boundary on purpose.

### Costs

- Each pipeline run: ~$0.50–$2.00 in Claude tokens depending on ticket complexity + retry count
- Vector store calls: negligible (Pinecone free tier ~1M ops/month)
- Bridge bot infra: $5–30/mo on Azure App Service / Heroku / Render for the 24x7 bot listener

For an interview demo, costs are trivial. For team-wide rollout, budget ~$50/eng/mo.

## Phasing

| Phase                                                | Effort      | Order                                              |
| ---------------------------------------------------- | ----------- | -------------------------------------------------- |
| 1. Verify ChatGPT + Cursor native MCP works end-to-end | ~1 hour    | First — proves the value loop without writing code |
| 2. Document client config snippets in PROVISIONING-NEW-BOARD.md | ~30 min | Right after phase 1                       |
| 3. Slack bridge (lowest-effort bridge to ship)       | ~1 day      | Pick this if your interview audience uses Slack    |
| 4. Teams bridge (Copilot Studio variant)             | ~1.5 days   | Pick this if the audience is M365-shop             |
| 5. Shared bridge library (`bender-bridge-py`)         | ~1 day extra | Only if shipping 2+ bridges                       |
| 6. JIRA webhook → chat (for the long-running UX)    | ~0.5 day    | Quality-of-life polish for any bridge              |

**Total for a polished team rollout: ~3–5 days.** For an interview demo, just phase 1 (~1 hour, no code) is plenty.

## Demo angle (interview pitch)

The strong version of this story for the interviewers: *"Multi-tenant Bender already supports any MCP-native client out of the box — here's me adding a Punch ticket from ChatGPT in 30 seconds. For non-MCP platforms like Teams or Slack, here's the bridge architecture and a 1-day spike doc. The composability matters because the whole point of the pipeline is to let non-engineers turn natural-language ideas into shipped code, and you can't do that if the only client is Claude Desktop."*

Showing a live ChatGPT or Cursor session creating a JIRA ticket via `create_ticket` and triggering `process_ticket` against the deployed punch-pwa board is the demo punchline. The bridge bots are the *"and here's the next step"* — concrete enough to be credible, not over-engineered for the demo itself.
