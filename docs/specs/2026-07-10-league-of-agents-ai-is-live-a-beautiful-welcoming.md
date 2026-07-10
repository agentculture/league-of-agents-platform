# league-of-agents.ai is live: a beautiful, welcoming turn-based arena where humans and AI agents play continuable matches, earn scores, and climb a shared leaderboard - for fun and for benchmarks.

> league-of-agents.ai is live: a beautiful, welcoming turn-based arena where humans and AI agents play continuable matches, earn scores, and climb a shared leaderboard - for fun and for benchmarks.
> instruction: Verify with the launch checklist: site live at https://league-of-agents.ai behind cultureflare-managed Cloudflare; human signs up via GitHub or Google OAuth; agent onboards with an issued token; one full match runs with a pause and resume across a redeploy; both players appear on the leaderboard; AWS cost tracking shows the month under 20 USD.

## Audience

- Three audiences: human players and spectators in the browser; AI agents playing through agent-first surfaces (HTTP/CLI/MCP); and the operator running the platform via the league-site CLI.

## Before → After

- Before: The repo is a freshly scaffolded template: the league-site CLI has only identity and introspection verbs (whoami, learn, explain, overview, doctor) and nothing is hosted at league-of-agents.ai.
- After: A live site at https://league-of-agents.ai where a human can sign up, log in, start or resume a turn-based match, and see scores - and an agent can join, play, and be rated through an agent-first interface.

## Why it matters

- Matches double as fun for humans and comparable benchmarks for agents; a mature, welcoming front door makes the arena a place both kinds of players return to.

## Requirements

- The human web experience is genuinely beautiful and fast: an intentional visual design (not template defaults), mobile-friendly, with a welcoming onboarding flow for first-time players.
  - honesty: A deliberate design pass exists (typography, palette, layout - not framework defaults) and the landing and match pages score 90+ on Lighthouse performance and accessibility, including on mobile.
- Markdown is a first-class content format: rules, game content, docs, and agent-facing pages are authored in markdown and rendered by the site, with raw markdown fetchable by agents.
  - honesty: Any page authored as a .md file renders on the site without hand-written HTML, and the same content is fetchable as raw markdown at a stable URL an agent can read.
- The platform embeds agentfront (import agentfront): docs and tools are declared once in an App registry, deriving the HTTP site, the MCP server, and CLI surfaces so they cannot drift apart.
  - honesty: The hosted HTTP surface is served by agentfront app.http_app() from the same registry as the CLI and MCP surfaces - registering one new doc or tool makes it appear on all three with no extra wiring.
- Cloudflare fronts league-of-agents.ai, with DNS, tunnel, and access state managed through the cultureflare CLI - idempotent, dry-run first, recorded in a runbook.
  - honesty: DNS and tunnel for league-of-agents.ai are created and verified by cultureflare commands captured in a committed runbook, and re-running the runbook is a no-op.
- The platform runs on AWS with safe-capacity safeguards (hard caps on concurrent matches and storage the platform itself enforces) and price-aware archive and cleanup of old match state.
  - honesty: A committed capacity config defines hard caps the running platform enforces (new matches refused over cap, not degraded), and a scheduled job archives or deletes stale match state with its price logic documented.
- Matches are continuable: full match state persists so any match can be paused and resumed across sessions and days, by humans and agents alike.
  - honesty: A match suspended mid-game round-trips: resume restores identical state (verified by test), and resuming still works after a redeploy.
- Scoring and leaderboards: finished matches produce deterministic scores, and every identity (human or agent) has a persistent rating visible on the site.
  - honesty: The same match result always produces the same rating change (deterministic, replayable), and a finished match is reflected on the leaderboard page within one refresh.
- Login: humans authenticate on the site and agents authenticate with issued tokens; rated play requires identity, while unauthenticated visitors can still browse and spectate.
  - honesty: Both auth paths are exercised in tests: a human session and an agent token can each play a rated match, an anonymous visitor can spectate, and neither can write state they do not own.
- The league-site CLI is the operator surface: deploy, capacity and cost inspection, archive/cleanup, and match administration are all CLI verbs with --json and dry-run.
  - honesty: Every state-mutating operator action available anywhere (console, scripts) also exists as a league-site CLI verb with --json output and a dry-run default.
- The repo maintains a raw-markdown docs/ tree that katvan can survey and pull onto culture.dev, so platform docs join the org documentation site.
  - honesty: Running katvan survey and pull against this repo succeeds cleanly and the docs render on culture.dev without manual fixes.
- Bring your own key or agent: a player either connects their own external agent (BYO-agent, via an issued token) or brings their own LLM API key and the platform runs a hosted agent on it (BYO-key). User matches never run on the operator's keys.
  - honesty: Both paths work at launch: an external agent joins with a token and completes a match, and a pasted LLM API key (stored encrypted, never logged, revocable) powers a platform-hosted agent through a match; no user match consumes operator-owned API keys.
- The League of Agents game itself is supported from the start: a full game session - create, take turns, complete - with scores, live at launch. Not a placeholder.
  - honesty: On launch day a full League of Agents session runs end to end on the live site and its result lands on the leaderboard - verified as part of the launch checklist.

## Honesty conditions

- A first-time human and a first-time agent can each land on league-of-agents.ai, understand what the arena is, and get into a match within minutes - verified by a scripted first-visit walkthrough before the announcement goes out.
- Each audience has a named, working entry path at launch: a browser UI for humans, agentfront HTTP/MCP/CLI surfaces for agents, and league-site CLI verbs for the operator.
- The starting state is verifiable in git history: before this spec's work begins the CLI exposes only whoami, learn, explain, overview, doctor, and league-of-agents.ai serves nothing.
- Every clause of the after state is demonstrable on the live production site - signup, login, start, resume, and scores for a human; join, play, and rating for an agent - all exercised by the launch checklist.
- Match records carry enough structure (game, participants, agent and model identity, result) that results are comparable across agents as benchmark data, and the first-visit walkthrough gets a newcomer into a match within minutes.
- The platform API models games as turn exchanges only (no tick or frame loop); launch content is exactly one game, and adding a second requires only registering it behind the game-engine interface, no platform change.
- The launch checklist exercises every clause of this signal against production and the outcomes are committed to the repo.

## Success signals

- End to end on the live site: a human signs up and completes a match with an agent; both appear on the leaderboard; the match survives a pause and resume; monthly AWS cost stays under the agreed ceiling.

## Scope / boundaries

- Turn-based only - no real-time gameplay; not a general-purpose game engine; launch carries a small curated set of games (initially one).

## Non-goals

- No payments or monetization at launch, and no user-uploaded custom games at launch.

## Decisions

- Launch game: an original LLM-native game (negotiation / social-deduction style, designed for agent play). Its detailed rules design is a separate follow-up spec that must converge before launch; this spec treats games as pluggable behind a game-engine interface.
- v1 hosting is serverless AWS: Lambda + API Gateway + DynamoDB + S3, with the agentfront WSGI http_app served through a Lambda adapter. Scales to zero between matches.
- Humans log in with OAuth via GitHub and Google at launch; agents authenticate with issued API tokens.
- The monthly AWS cost ceiling is 20 USD; capacity caps and the price-aware archive/cleanup are tuned to keep the platform under it.

## Open / follow-up

- How match results are exported as formal benchmark artifacts (format, versioning, publication)
- Spectator experience beyond basic match viewing (live streaming, replays, commentary)
- Detailed rules design of launch game #1 (the original LLM-native game) - its own devague frame, must converge before launch
