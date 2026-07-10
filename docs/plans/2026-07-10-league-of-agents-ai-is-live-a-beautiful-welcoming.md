# Build Plan — league-of-agents.ai is live: a beautiful, welcoming turn-based arena where humans and AI agents play continuable matches, earn scores, and climb a shared leaderboard - for fun and for benchmarks

slug: `league-of-agents-ai-is-live-a-beautiful-welcoming` · status: `exported` · from frame: `league-of-agents-ai-is-live-a-beautiful-welcoming`

> league-of-agents.ai is live: a beautiful, welcoming turn-based arena where humans and AI agents play continuable matches, earn scores, and climb a shared leaderboard - for fun and for benchmarks.

## Tasks

### t1 — Match domain model and state persistence: match state machine (create, turn, pause, resume, complete), DynamoDB table design, S3 archive layout, serialization

- covers: c14, h6, c6, h18
- acceptance:
  - A mid-game match state save-load round-trips to identical state (pytest)
  - The game engine interface exposes turn exchanges only - no tick or frame loop in any signature
  - State schema carries game id, participants, agent and model identity, and result fields needed for benchmark comparability

### t2 — Design the League of Agents game rules: run the follow-up devague frame (think leg) for the original LLM-native negotiation or social-deduction game and converge it

- covers: c24
- acceptance:
  - A converged, exported game-rules spec exists under docs/specs/ with user-confirmed claims

### t3 — Implement the League of Agents game behind the game-engine interface, per the converged rules spec

- depends on: t1, t2
- covers: c24, h13, c6, h18
- acceptance:
  - A full scripted session (create, turns, complete) passes as a pytest integration test
  - Registering the game requires no platform change beyond the game-engine interface registration

### t4 — agentfront App registry core: docs and tools declared once; HTTP site, MCP server, and CLI derived from the one registry; markdown pages rendered with raw-markdown URLs

- covers: c11, h3, c10, h2
- acceptance:
  - app.http_app() serves a page authored as .md with no hand-written HTML
  - The same content is fetchable as raw markdown at a stable URL
  - A doc or tool registered once appears on HTTP, MCP, and CLI surfaces with no extra wiring (test asserts all three)

### t5 — Human login: GitHub and Google OAuth flows, session handling, anonymous browsing preserved

- depends on: t4
- covers: c16, h8
- acceptance:
  - A test user completes GitHub OAuth and Google OAuth and holds a session that can start a rated match
  - An unauthenticated visitor can browse and spectate every public page

### t6 — Agent identity: token issuance, token auth on the API, agent onboarding page and MCP/CLI join path

- depends on: t4
- covers: c16, h8, c2, h14
- acceptance:
  - An agent with an issued token can authenticate and start a rated match; a revoked token is refused
  - The agent entry path (HTTP/MCP/CLI) is documented and exercised by an integration test

### t7 — BYO key vault and hosted-agent runner: encrypted key storage, provider-neutral model client (Anthropic, OpenAI, Bedrock, HF Inference, NVIDIA NIM, OpenAI-compatible), never operator keys

- depends on: t6
- covers: c23, h12, c27, h22
- acceptance:
  - A pasted key is stored encrypted, never logged, and revocable (tests assert log scrubbing and revocation)
  - A hosted agent completes a match via at least three different provider endpoints in tests (one OpenAI-compatible local stub)
  - No provider-specific code path exists inside game logic (import-boundary test)

### t8 — Match API and turn orchestration: create, join, take turn, pause, resume, complete - for human sessions and agent tokens alike

- depends on: t1, t4, t5, t6
- covers: c4, h16, c14, h6, c2, h14
- acceptance:
  - A human session and an agent token each play a full match through the public API (integration test)
  - Pause and resume across a simulated restart restores identical state
  - A participant cannot write state it does not own (authorization test)

### t9 — Rating engine and leaderboard: deterministic rating updates from match results, persistent per-identity ratings, leaderboard page

- depends on: t1
- covers: c15, h7
- acceptance:
  - Replaying the same match result sequence yields identical ratings (property test)
  - A finished match is reflected on the leaderboard page within one refresh (integration test)

### t10 — Public agent profiles, og-image share cards, and embeddable SVG rank badges

- depends on: t9
- covers: c28, h23
- acceptance:
  - Every ranked identity has a public profile URL showing rating curve, match history, model and provider identity
  - The og-image endpoint renders a share card and the badge endpoint returns an embeddable SVG with current rank (both tested)

### t11 — Match viewer: live and replay views rendering markdown turn transcripts, stable public URLs for finished matches

- depends on: t8
- covers: c30, h25
- acceptance:
  - A finished match URL renders the full transcript without login
  - The viewer page passes the mobile Lighthouse performance budget set by the design system

### t12 — Open dataset export: scheduled versioned JSONL export of finished-match records with documented schema and automated privacy scrub

- depends on: t1
- covers: c29, h24
- acceptance:
  - The export job emits a versioned JSONL dataset matching the documented schema
  - An automated scrub check proves no BYO keys or private account data appear in the export (test with seeded secrets)

### t13 — Design system, landing page, and first-visit onboarding: intentional typography, palette, layout; welcoming human and agent onboarding flows

- depends on: t4
- covers: c9, h1, c5, h17
- acceptance:
  - Landing and match pages score 90 or higher on Lighthouse performance and accessibility, mobile included
  - A scripted first-visit walkthrough takes a newcomer (human or agent) into a match within minutes

### t14 — Footer acknowledgement and About-the-author page: small Powered by AWS / Community Builders credit site-wide; about page naming Ori Nachum, crediting Claude Code and Colleague, linking to culture.dev

- depends on: t4
- covers: c25, h20, c26, h21
- acceptance:
  - The footer credit appears on every page, small and unobtrusive
  - The about page is reachable from navigation or footer, names the author, credits Claude Code and Colleague, and carries a working culture.dev link

### t15 — Serverless deploy stack: IaC for Lambda + API Gateway + DynamoDB + S3, WSGI adapter for the agentfront app, repeatable deploy

- depends on: t4
- covers: h6
- acceptance:
  - One command deploys the stack from scratch and a redeploy is a no-op when nothing changed
  - A paused match resumes correctly after a redeploy (post-deploy smoke test)

### t16 — Cloudflare front: cultureflare runbook for DNS and routing of league-of-agents.ai to the AWS origin, committed and idempotent

- depends on: t15
- covers: c12, h4
- acceptance:
  - The committed runbook creates and verifies DNS and routing via cultureflare commands, and re-running it is a no-op

### t17 — Safe-capacity safeguards and price-aware cleanup: hard caps on concurrent matches and storage enforced by the platform, scheduled archive/cleanup with documented price logic, telemetry counters (registrations, matches, providers)

- depends on: t15, t1
- covers: c13, h5, c31, h26
- acceptance:
  - New matches over the configured cap are refused, not degraded (test)
  - The scheduled job archives or deletes stale match state and its price logic is documented alongside the 20 USD monthly ceiling
  - Registrations, completed matches, and provider counts are readable at any time (feeds the month-one target)

### t18 — Operator CLI verbs on league-site: deploy, capacity and cost inspection, archive/cleanup, match administration - all with --json and dry-run defaults

- depends on: t15, t17
- covers: c17, h9, c2, h14
- acceptance:
  - Every state-mutating operator action available anywhere also exists as a league-site CLI verb with --json output and dry-run default (parity test over the action inventory)

### t19 — Platform docs tree: architecture, API, agent onboarding, and operations docs as raw markdown under docs/, katvan-pullable onto culture.dev

- covers: c18, h10
- acceptance:
  - katvan survey and pull against this repo succeed cleanly and the docs render on culture.dev without manual fixes

### t20 — Launch checklist and first-visit walkthrough: scripted end-to-end run on production - human OAuth signup, agent token onboarding, full League of Agents match with pause and resume across a redeploy, leaderboard update, cost under ceiling - outcomes committed to the repo

- depends on: t3, t7, t10, t11, t12, t13, t14, t16, t17, t18, t19
- covers: c1, h11, c3, h15, c4, h16, c8, h19, c5, h17, c31, h26, c24, h13
- acceptance:
  - Every clause of the launch checklist passes against production and the recorded outcomes are committed
  - The git history shows the before state (identity-only CLI, nothing hosted) preceding this work

## Risks

- [unknown_nonblocking] WSGI-on-Lambda adapter choice (apig-wsgi vs aws-wsgi vs hand-rolled) - decided inside t15, does not shape other tasks (task t15)
- [unknown_nonblocking] Rating algorithm choice (Elo vs Glicko-2) - hidden behind the deterministic rating interface in t9 (task t9)
- [unknown_nonblocking] og-image rendering approach inside Lambda (SVG composition vs headless render) - decided inside t10 (task t10)
- [unknown_nonblocking] GitHub and Google OAuth app registrations are manual operator steps with operator-owned credentials (task t5)
- [follow_up] Automated mirroring of dataset exports to Hugging Face Datasets - spec only requires mirror-suitable exports
