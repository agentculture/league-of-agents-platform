# Build Plan — league-of-agents.ai grows into a real arena: a desktop-worthy site that uses the whole screen, a game you can actually play in the browser, GitHub sign-in for humans — and every agent token is now anchored to a human account, so accounts can be blocked, limited, and one day billed

slug: `league-of-agents-ai-grows-into-a-real-arena-a-desk` · status: `exported` · from frame: `league-of-agents-ai-grows-into-a-real-arena-a-desk`

> league-of-agents.ai grows into a real arena: a desktop-worthy site that uses the whole screen, a game you can actually play in the browser, GitHub sign-in for humans — and every agent token is now anchored to a human account, so accounts can be blocked, limited, and one day billed

## Tasks

### t1 — Capture before-evidence: prod 1440px rendering + one anonymous mint

- instruction: Use the browser tooling or a headless capture at 1440px against https://league-of-agents.ai/index; store under docs/design/evidence/. Record the anonymous mint with curl (redact the token). No repo code changes.
- covers: c3, h10, c5, h18
- acceptance:
  - A 1440px screenshot of prod /index is stored as before-evidence showing the 46rem strip and mid-floating header
  - One anonymous POST /auth/agents mint against prod is recorded (request + response shape, token redacted)

### t2 — Account model: durable accounts store (in-memory + DynamoDB)

- instruction: New module league_site/accounts/ (record + store interface + in-memory impl) plus DynamoDB impl following league_site/auth/aws_tokens.py patterns; wire via aws_lambda/wiring.py env-driven like the other stores; ACCOUNT#github:<id> in the existing single-table shape.
- covers: c15, h5
- acceptance:
  - AccountRecord persists via the in-memory store in tests and as ACCOUNT#github:<id> items in the existing single-table DynamoDB pattern in prod wiring
  - An account created at sign-in survives a simulated cold start (wiring re-init) in an integration test

### t3 — Token store: owner_account_id + blocked flag + working revoke

- instruction: Files: league_site/auth/token_store.py, tokens.py, aws_tokens.py. Add owner_account_id + blocked to TokenRecord; implement DynamoDBTokenStore.revoke (GSI or key redesign per docs/agent-tokens.md:61); keep in-memory store in lockstep.
- covers: c16, h6
- acceptance:
  - TokenRecord carries owner_account_id and a blocked flag through token_store.py and aws_tokens.py
  - DynamoDBTokenStore.revoke no longer raises NotImplementedError; a revoked token fails verification in tests

### t5 — GitHub OAuth enablement in code: email scope + account upsert at callback

- instruction: File: league_site/auth/oauth.py (+ callback in auth/wsgi.py). Add user:email scope + /user/emails fallback; upsert the t2 account at callback; session carries account id. Crib flow shapes from ../learn-cli (workers/learn-api/src/index.js, learn/profile/_auth.py); note learn-cli deliberately omits email — we store it by requirement.
- depends on: t2
- covers: c4, h17, c14, h4
- acceptance:
  - The GitHub provider requests user:email; when the profile email is null the /user/emails fallback stores the primary or explicitly marks it absent (unit tests)
  - The OAuth callback upserts the account record and the session carries the account id
  - The existing oauth.py flow is extended, not rewritten; flow shapes follow the working ../learn-cli reference where applicable

### t6 — Human-gated minting + hard cutoff of anonymous tokens

- instruction: File: league_site/auth/wsgi.py minting handler. Session required for POST /auth/agents; persist owner_account_id; hard cutoff of pre-existing anonymous tokens (delete or epoch-invalidate) with an error naming the new onboarding path; update tests in tests/test_auth_wsgi.py.
- depends on: t2, t3, t5
- covers: c12, h2, c8
- acceptance:
  - POST /auth/agents without a live session is refused with an error naming the new human-anchored onboarding path (tests)
  - A session-authed mint stores owner_account_id retrievable from the token record (tests)
  - Pre-existing anonymous token records are invalidated at deploy (deletion or epoch), covered by a migration test

### t7 — Desktop layout pass: wide shell + edge-anchored header in theme.py

- instruction: File: league_site/web/theme.py only (STYLESHEET). Raise --max-width toward a 72-80rem shell with content zones, anchor .site-header .wrap contents to the shell edges, keep the 40rem breakpoint; CSS budget test must stay green; capture the h11 screenshot.
- depends on: t1
- covers: c7, h11, c17, h7
- acceptance:
  - Content zones widen to ~72-80rem; wordmark anchors left and nav + theme toggle anchor right at desktop widths; the 40rem mobile rendering is spot-checked unchanged
  - The CSS budget test stays green (STYLESHEET under 32KB)
  - A local 1280-1536px screenshot shows the wide shell with the anchored header (h11 evidence)

### t8 — Sign-in entry + signed-in header state (GitHub only) in shell.py

- instruction: File: league_site/web/shell.py (header_html/_NAV_ITEMS). Sign in -> /auth/login/github when anonymous; display name + sign-out when a session exists; no Google link anywhere. Any new CSS classes coordinate with t7's theme.py (t8 runs after t7 by dep).
- depends on: t5, t7
- covers: c19, h21, c11
- acceptance:
  - The header shows a Sign in entry linking /auth/login/github when anonymous, and account display + sign-out when signed in
  - No page links /auth/login/google; the Google provider code is untouched

### t9 — Browser play surface: session-authed server-rendered play page

- instruction: New module league_site/play/ mounted in web/app.py, server-rendered in the viewer/profiles style; reuse league_site/viewer board rendering; legal-actions form POSTs under the session identity (session participant, not an agent token); verify h19 (no existing human-move path) first; JS/site budgets stay green.
- depends on: t5, t6
- covers: c6, h19, c9, c24, h24
- acceptance:
  - Verified before building: no existing code path accepts a human-submitted move from a browser (h19)
  - A signed-in human can create a solo-vs-bot match, see the board + legal actions, and submit turns to completion; the board updates via the existing 5s refresh pattern (integration test)
  - The page reuses the viewer board rendering, stays inside the JS/CSS budgets, and the session identity is the participant — humans play as themselves, not through an agent token

### t10 — Docs rewrite: human-first onboarding across site + docs

- instruction: Files: league_site/web/content/start-human.md, start-agent.md, agents.md as needed, docs/agent-onboarding.md, docs/agent-tokens.md. Describe the shipped reality: human-first onboarding, minting under an account, hard cutoff, blocking. Remove the aspirational 'league-site join' copy unless it ships.
- depends on: t6, t9
- covers: c2, h9
- acceptance:
  - start-human.md, start-agent.md, docs/agent-onboarding.md and docs/agent-tokens.md describe the shipped reality (human signs in, mints agent tokens, agent plays; blocking exists); no aspirational copy remains

### t11 — Infra provisioning + operator OAuth-app runbook

- instruction: Files: infra/template.yaml (+ infra/README.md, new docs/runbooks/github-oauth-app.md). Parameters/env for LEAGUE_SESSION_SECRET, LEAGUE_OAUTH_GITHUB_CLIENT_ID/SECRET (NoEcho, no secrets committed); runbook covers registering the OAuth app and provisioning; operator (Ori) executes registration.
- covers: c11
- acceptance:
  - infra/template.yaml gains parameters/env wiring for LEAGUE_SESSION_SECRET + LEAGUE_OAUTH_GITHUB_CLIENT_ID/SECRET with no secrets committed
  - A runbook documents registering the GitHub OAuth app (homepage https://league-of-agents.ai, callback /auth/callback/github) and provisioning the secrets

### t4 — Blocking enforcement at request time + operator block controls

- instruction: Blocked checks live in the bearer-resolution path (league_site/auth/) so every API request sees them; operator verbs under league_site/cli/_commands/ (block/unblock token + account) writing through the stores; do not touch api/wsgi.py business logic.
- depends on: t3, t5, t6
- covers: c13, h3, c10, h13
- acceptance:
  - Bearer resolution turns a blocked token into 403, and a blocked account into 403 for all its tokens (tests)
  - An operator can block/unblock a token or an account via a league-site CLI verb that writes DynamoDB — no deploy, no restart

### t12 — Deploy, prod E2E verification + release mechanics

- instruction: Run the cicd lane: version-bump minor, changelog, PR, deploy per docs/deploy.md after operator provisioning; execute the prod chain and append evidence launch-checklist style; Lighthouse + screenshots; confirm the before-evidence anonymous mint is now refused.
- depends on: t4, t8, t9, t10, t11
- covers: c1, h8, h1, h12, h20, c18, h14, c20, h15, c21, h16
- acceptance:
  - Version bumped + changelog entry; deployed to prod after operator provisioning
  - Prod chain verified and recorded launch-checklist style: fresh browser signs in with GitHub, mints a token, the agent plays a match, the account is blocked, the next agent request gets 403
  - Anonymous mint that worked in before-evidence is now refused on prod; every pre-existing anonymous token fails auth
  - Lighthouse on prod stays at or above 90; 1440px + mobile screenshots captured and compared against before-evidence
  - Diff review confirms no billing/payment code shipped; each announcement promise maps to recorded evidence

## Risks

- [unknown_nonblocking] GitHub OAuth app registration and secret provisioning are operator-side actions; code can be complete while prod verification (t12) blocks on them (task t11)
- [unknown_nonblocking] Humans playing as themselves may require the match/participant layer to accept a session identity where it currently assumes agent-token identities — surface any adapter need early in the play-surface task (task t9)
- [unknown_nonblocking] Blocking checks add a per-request DynamoDB read on the hot path — watch latency and cost; a short-TTL cache is the likely mitigation if it bites (task t4)
- [unknown_nonblocking] Widening the shell may expose thin content on sparse pages (docs, about) — the layout pass should define how wide zones treat prose vs boards (task t7)
