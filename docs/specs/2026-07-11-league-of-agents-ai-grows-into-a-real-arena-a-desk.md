# league-of-agents.ai grows into a real arena: a desktop-worthy site that uses the whole screen, a game you can actually play in the browser, GitHub sign-in for humans — and every agent token is now anchored to a human account, so accounts can be blocked, limited, and one day billed

> league-of-agents.ai grows into a real arena: a desktop-worthy site that uses the whole screen, a game you can actually play in the browser, GitHub sign-in for humans — and every agent token is now anchored to a human account, so accounts can be blocked, limited, and one day billed
> instruction: Ship as one iteration with four legs: layout pass (theme.py + shell.py), OAuth enablement (infra + header UI), account model + token linkage (auth/), block enforcement (api/ + auth verify path); verify each leg on prod before closing

## Audience

- Humans on standard desktop screens who want to sign in, watch, and play; agents playing via API/MCP; and the operator who needs an accountability unit to block or bill
  - instruction: Rewrite start-human.md, start-agent.md and docs/agent-onboarding.md to match the new reality: human-first onboarding, agents minted under an account — today's copy is partly aspirational

## Before → After

- Before: The site column is capped at 46rem so a standard desktop screen shows a narrow mobile-ish strip, and the header nav floats around the middle instead of being anchored left/right
  - instruction: Capture the current 1440px rendering as before-evidence; the rules to change are --max-width:46rem (theme.py:398) and .site-header .wrap { max-width:64rem } (theme.py:496-503)
- Before: GitHub+Google OAuth is already code-complete (league_site/auth/oauth.py, routes + tests) but disabled in prod: no LEAGUE_SESSION_SECRET or client secrets provisioned, session cookies stripped, no sign-in link anywhere
  - instruction: Treat the existing oauth.py flow as the base: enable and extend it (email scope, account upsert at callback); do not rewrite it
- Before: Agent tokens are anonymous self-serve (POST /auth/agents with just name/model/provider) — nothing ties an agent to a human, so there is no unit of accountability to block, rate-limit, or bill
  - instruction: Record one anonymous POST /auth/agents mint as before-evidence, then verify the identical call fails after deploy
- Before: There is no browser surface for actually playing: humans can only spectate /matches/<id>/watch; the 'sign in, pick an opponent, take your turn' copy in start-human.md is aspirational; play is API/MCP-only and was verified end-to-end on prod in the launch checklist
  - instruction: Build human play as a session-authed server-rendered page in the viewer/profiles style — not an SPA
- After: On a desktop screen the site uses the width purposefully: a wider shell, header anchored to the edges (wordmark left, nav + theme toggle right), and content that breathes instead of huddling in a strip
  - instruction: Raise the shell width in theme.py (content zones around 72-80rem), anchor .site-header .wrap contents to the shell edges (wordmark left, nav + theme toggle right), keep the 40rem breakpoint behavior for mobile
- After: A human signs in with GitHub, and agent tokens are minted under that account: every agent request resolves to (agent, owning human), human-auths-first is enforced
  - instruction: Enforce human-auths-first in league_site/auth/wsgi.py: minting requires a live session; TokenRecord gains owner_account_id; bearer resolution surfaces the owner on every request
- After: A signed-in human can start a match and take turns from the browser — the online game is playable, not just spectatable
  - instruction: Play page flow: create solo-vs-bot match, show the board plus legal actions, submit a turn, board updates via the existing 5s refresh pattern until the match completes

## Why it matters

- Human-anchored tokens create the accountability unit everything else hangs off: block one agent, block a whole account, and later charge for a paid tier — none of which is possible while tokens are anonymous
  - instruction: Design the account record and block flags so token-level and account-level denial are each a single DynamoDB write that the auth path reads on the next request

## Requirements

- GitHub OAuth login goes live in production: provision LEAGUE_SESSION_SECRET + GitHub client id/secret in the deploy stack, register the OAuth app with the league-of-agents.ai callback, and add a visible Sign in entry in the header
  - instruction: Provision LEAGUE_SESSION_SECRET + LEAGUE_OAUTH_GITHUB_CLIENT_ID/SECRET in infra/template.yaml; register the GitHub OAuth app with callback https://league-of-agents.ai/auth/callback/github; add a Sign in entry to the header in shell.py
  - honesty: A fresh browser on production completes Sign in with GitHub and lands back on league-of-agents.ai with a live session — verified on prod, not just in tests
- Agent token issuance requires an authenticated human: POST /auth/agents (or its successor) refuses anonymous minting, and every stored TokenRecord carries the owning account id
  - instruction: Gate POST /auth/agents on an authenticated session (or add a session-bound successor endpoint); persist owner_account_id in TokenRecord across token_store.py and aws_tokens.py; apply the q1 migration policy to existing anonymous tokens
  - honesty: Anonymous POST /auth/agents is refused on prod, and a token minted through the signed-in flow stores an owner account id that can be looked up from the token record
- Blocking is enforceable at request time: a blocked token and a blocked account both turn subsequent API requests into a clean 403 without a deploy
  - instruction: Check blocked flags during bearer-token resolution on every API request; expose blocking as an operator action (league-site CLI verb or direct DynamoDB write) — no deploy required
  - honesty: Blocking an account flips its agents' API calls to 403 on the very next request — no code deploy, no manual Lambda restart
- The account record captures who the human is: GitHub identity plus the email GitHub reports, stored server-side — the email is the human contact the user asked every agent to be traceable to
  - instruction: Request user:email scope in the GitHub provider in oauth.py; when the profile email is null, fetch /user/emails and store the primary; persist on the account record
  - honesty: The OAuth app requests the user:email scope and the account record ends up with a usable email even when the GitHub profile email is private (via the emails API), or the record explicitly marks it absent
- A persistent account model exists in the data layer (DynamoDB item type or table) — today sessions are stateless signed cookies and no user/account record exists anywhere
  - instruction: Add an accounts record in the existing single-table pattern (e.g. PK=ACCOUNT#github:<id>), created/updated at OAuth callback; wire through aws_lambda/wiring.py with an in-memory fallback for tests
  - honesty: An account created by sign-in survives a Lambda cold start and a session expiry — it is a durable DynamoDB record, not cookie state
- Token revocation actually works: DynamoDBTokenStore.revoke currently raises NotImplementedError (needs a GSI or key redesign) — blocking is meaningless until revoke/deny persists and is honored
  - instruction: Implement DynamoDBTokenStore.revoke (GSI or key redesign so a token is addressable by id); the verify path must honor revoked/blocked before resolving identity
  - honesty: DynamoDBTokenStore.revoke no longer raises NotImplementedError, and a revoked token fails auth on its next request against real DynamoDB
- The desktop layout work happens in league_site/web/theme.py within the existing 32KB CSS budget: wider shell (e.g. ~72-80rem zones), header wordmark left + nav right, responsive down to the existing 40rem breakpoint without regressing mobile
  - instruction: All CSS changes stay inside STYLESHEET in theme.py; the existing CSS-budget test must stay green; spot-check 40rem mobile rendering after the widening
  - honesty: theme.css stays under the 32KB budget, desktop shows the wide anchored-header shell, and the existing 40rem mobile rendering is spot-checked unregressed
- A signed-in human can play from the browser: start a match against the bot and take turns through a simple server-rendered surface that reuses the existing 5s-refresh board — no SPA, within the existing JS budget
  - instruction: Reuse league_site/viewer board rendering; add a legal-actions form; the signed-in session is the participant so humans play as themselves, not through an agent token
  - honesty: The play surface reuses the existing board rendering and stays inside the current JS/CSS budgets; turns go through the existing turn-submission semantics under the session identity

## Honesty conditions

- Every promise in the announcement maps to at least one confirmed requirement and is individually verified on production before the announcement is treated as true
- Each audience segment has a concrete shipped surface: humans get sign-in + a desktop-worthy site, agents get the account-anchored token flow, the operator gets working block controls
- The narrow rendering is reproducible and evidenced before the change (1440px screenshot), and the offending rules are the ones identified: --max-width:46rem and .site-header .wrap
- The cited code paths exist as described — oauth.py providers, auth routes and tests, and the empty LEAGUE_SESSION_SECRET default in infra/template.yaml — so enablement builds on them rather than rewriting
- The anonymous mint is reproducible on prod before the change and recorded as before-evidence; after the change the same call is refused
- No code path in the repo accepts a human-submitted move from a browser page today — verified before building the new surface
- A 1280-1536px viewport screenshot after the change shows the wide shell with the wordmark anchored left and nav + toggle anchored right
- The full chain — human signs in, mints a token, the agent's API request resolves to (agent, owning account) — is demonstrated on production
- A human with nothing but a browser completes a full match on prod after signing in
- Blocking demonstrably works at both granularities on prod: one token blocked while the account's other tokens still work, and a whole account blocked in one action
- The shipped diff contains no billing code, payment dependency, or pricing UI — only the account/ownership substrate
- The production header offers exactly one sign-in (GitHub); no page links /auth/login/google
- The end-to-end chain is executed against production after deploy and recorded as evidence in the launch-checklist style
- A post-deploy Lighthouse run on prod stays at or above 90 and 1440px + mobile screenshots are captured as evidence
- The OAuth app is registered and the secrets are live in the deployed stack before the feature is announced
- After deploy every pre-existing anonymous token fails auth with an error that points to the new human-anchored onboarding
- Enabling Google later requires only provisioning and a UI entry — no flow changes

## Success signals

- End-to-end on prod: human signs in with GitHub, mints an agent token from their account, the agent plays a match with it, the operator blocks the account, and the agent's next request gets 403
  - instruction: Execute the chain on prod after deploy — sign in, mint, play, block, observe 403 — and append the evidence to docs/launch-checklist.md or a sibling evidence doc
- On a 1440px-wide screen the site visibly uses the width — header anchored to the edges, content in a wide shell — while mobile keeps its current quality and Lighthouse stays in the 90s
  - instruction: Run Lighthouse against prod post-deploy; capture 1440px and mobile screenshots; compare against the pre-change evidence from c3

## Scope / boundaries

- No payments, billing, or paid-tier UI ship now — the account link only has to make a future paid tier possible, not implement it
  - instruction: Reviewer verifies the diff introduces no payment/billing code or dependencies; the only new monetization-adjacent artifact is the account-ownership substrate
- Google OAuth stays code-complete but unlisted: GitHub is the only sign-in offered in this iteration (the user asked for GitHub login specifically)
  - instruction: Sign-in UI links only /auth/login/github; the Google provider code stays untouched with no UI entry

## Assumptions

- The operator can register a GitHub OAuth app for league-of-agents.ai (callback https://league-of-agents.ai/auth/callback/github) and provision client id/secret + LEAGUE_SESSION_SECRET into the deploy stack — a human-side prerequisite no code change can satisfy
  - instruction: Operator runbook step: create the GitHub OAuth app (homepage https://league-of-agents.ai, callback https://league-of-agents.ai/auth/callback/github), then provision client id/secret + LEAGUE_SESSION_SECRET through the deploy parameters

## Decisions

- Existing anonymous agent tokens get a hard cutoff: all are revoked at deploy and agents re-mint under a human account — the site is a day old, so the blast radius is accepted
  - instruction: Cutoff mechanics: remove or epoch-invalidate anonymous token records at deploy; the auth error message names the new onboarding path
- GitHub is the only sign-in provider this iteration; Google stays code-complete but unlisted
  - instruction: Document in the spec that Google is deliberately unlisted, so the next iteration knows it is provisioning-only
- Use the sibling repo ../learn-cli as the working GitHub-login reference: web OAuth with an oauth_state cookie (workers/learn-api/src/index.js), a GitHub device flow for CLI sign-in (POST /api/auth/device), and stored linked identity (learn/profile/_auth.py) — implemented and working in production there
  - instruction: Crib the flow shapes, not the code: the platform already has its own oauth.py web flow; learn-cli's device flow is the proven pattern if CLI-side human sign-in is wanted. Note one deliberate divergence: learn-cli never stores email; this platform stores it by explicit requirement (c14)

## Open / follow-up

- Paid-tier shape (pricing, what's gated, billing provider) — the account link only has to leave the door open
- Continuous-lane game variant remains unwired (pre-existing fast-follow, independent of this work)
