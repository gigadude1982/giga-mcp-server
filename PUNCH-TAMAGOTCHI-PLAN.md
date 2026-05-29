# Punch Tamagotchi — planning doc

Working doc capturing the decisions and remaining steps to add a tamagotchi PWA as a new board on giga-mcp-server. Delete this file once `PUNCH-1` ships and the board is live.

Started 2026-05-15.

## Identifiers (locked in)

| Thing                            | Value                              |
| -------------------------------- | ---------------------------------- |
| Character name                   | Punch (the monkey)                 |
| GitHub repo                      | `gigadude1982/punch-pwa`           |
| JIRA project key                 | `PUNCH` (tickets `PUNCH-1`, …)     |
| PWA app subdomain                | `punch.gigacorp.co`                |
| MCP server subdomain             | `mcp.punch.gigacorp.co`            |
| Board ID in `boards.ts`          | `punch-pwa`                        |
| MCP server name                  | `punch-mcp-server`                 |

## Stack (v1 PWA)

- **Vite + React + TypeScript**
- **PWA from day one** via `vite-plugin-pwa` — manifest + service worker. iOS users can "Add to Home Screen" for fullscreen / app-icon UX. Survives brief offline windows.
- **Jest + @testing-library/react** for tests. Chosen over Vitest to minimize drift from the Jest-tuned agent prompts in `pipeline/agent_prompts.py` (`jest.fn()`, `jest.spyOn()`, afterEach mock restore patterns).
- **DOM/CSS rendering, no canvas in v1.** Sprite sheets via CSS `background-position` + `steps()` keyframes for classic tamagotchi animation. Framer Motion for reactive transitions. Lottie only if real After Effects assets show up later.
- **Game state persistence** in `localStorage` (or `IndexedDB` if the save shape outgrows it) so Punch's hunger/happiness survives between visits.

## Why these choices

- **PWA over native v1:** zero Apple Developer fee, no Xcode, no review cycle, instant deploy via push-to-main. Reuses the existing React-web agent prompts as-is.
- **DOM over canvas:** one character on screen + a few buttons doesn't need canvas. DOM gives us Testing Library coverage, accessibility, and the agent prompts already know how to write `data-testid` + RTL queries. Canvas is opaque to all of that.
- **Jest over Vitest:** the prompts are Jest-flavored. Vitest is ~95% compatible but the 5% would create noise on every PR.

## Future notes (do NOT do in v1)

- **Canvas-confetti exception:** when celebratory effects come up (Punch evolving, hitting a happiness milestone), drop in `canvas-confetti` or `tsParticles` for *that effect only*, layered over the DOM game. Do NOT migrate the rest of the game to canvas.
- **v2 = React Native** for App Store / Play Store + real push notifications. Trigger: when push reminders ("Punch is hungry!") become load-bearing for retention. User has a MacBook + is willing to pay the $99/year Apple Developer fee. Business logic / hooks / state transfer cleanly; the UI layer is a rewrite (no DOM, no CSS — JS-object styling, native components).
- **Agent prompts are React-web-only today.** `pipeline/agent_prompts.py` embeds PropTypes, CSS modules, JSX/Prettier rules, React 17+ JSX transform avoidance. Works fine for v1. Before v2 (React Native) we need a rule-pack split keyed off `repo_config.py`'s `language` field — estimated ~1-2 days. See `CLAUDE.md` "Things that bite" section.

## Setup steps (in dependency order)

User-only steps marked **[user]**. Code changes marked **[code]** can be done by Claude in the next session.

1. **[user]** Create GitHub repo `gigadude1982/punch-pwa` (private or public, your call).
2. **[user]** Create JIRA project with key `PUNCH` in the Atlassian instance.
3. **[code]** Scaffold Vite + React + TS app in the new repo. Add `vite-plugin-pwa` config, manifest stub, basic favicon. Initial commit on `main` so the pipeline has a base branch to fork from.
4. **[code]** Add `.giga-pipeline.json` to the new repo:
   ```json
   {
     "language": "javascript",
     "test_framework": "jest",
     "test_command": "npm test -- --watchAll=false",
     "source_dirs": ["src"],
     "test_dirs": ["src"],
     "branch_prefix": "auto/"
   }
   ```
   (Tests live alongside source for React projects per the existing prompt convention.)
5. **[code]** ~~Add a board entry to `infra/config/boards.ts`.~~ **Done** — entry added with `vectorEnabled: true` and `jiraUrl` pointing at the gigacorporation Atlassian instance. If PUNCH ends up living in its own Atlassian instance, swap `jiraUrl` + `jiraUsername` before `cdk deploy`.
6. **[user]** Create `.env.punch-pwa` locally with the board's secrets, then run `scripts/setup-ssm.sh` to push them to SSM as SecureString parameters.
7. **[user]** `cd infra && npx cdk deploy` to provision the new App Runner service.
8. **[user]** Point `punch.gigacorp.co` DNS at the new App Runner service URL (CNAME record).
9. **[code]** File `PUNCH-1` ticket — suggested first ticket: "Scaffold game shell with Punch idle sprite, hunger meter, and feed button." Then `process_ticket("PUNCH-1")` and watch the pipeline plan it.

## Resolved decisions (2026-05-19)

- **Sprite art:** 🐒 (U+1F412) as baby Punch placeholder; 🦧 (U+1F9A7) as the evolved form. Evolution triggers when all three stats stay above a threshold for a streak (exact rule TBD during PUNCH-1 implementation). Replacing the emoji with a real sprite sheet later is a render-layer swap — no game-logic changes.
- **Decay model:** session-based. On page load, compute time-since-last-visit from `localStorage` and decay stats by that delta. No `setInterval` heartbeat. Matches the original tamagotchi feel and avoids background-tab edge cases.
- **Stat model:** hunger + happiness + energy. Three stats, three actions (feed, play, sleep). Slightly more game logic than hunger-only but lines up with the classic triad and gives the evolution mechanic something to gate on.
