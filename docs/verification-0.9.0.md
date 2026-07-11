# Verification — 0.9.0 desktop arena + human-anchored auth (executed 2026-07-11)

Every check below was run against **production**
(<https://league-of-agents.ai>, stack `league-of-agents-platform-prod`,
account 435593604218, us-east-1) or a local instance of the same composed
app, during the 0.9.0 rollout. Captured output is inlined. This closes the
[desktop-arena spec](specs/2026-07-11-league-of-agents-ai-grows-into-a-real-arena-a-desk.md)'s
success signals (c20/h15, c21/h16) and the per-requirement honesty
conditions. Style follows [launch-checklist.md](launch-checklist.md).

## Desktop layout (c3/h10 before → c7/h11, c17/h7, c21/h16 after)

- [x] **Before-evidence captured pre-change** —
  [design/evidence/2026-07-11-before-evidence.md](design/evidence/2026-07-11-before-evidence.md):
  1440px screenshot shows the 46rem strip and mid-floating header.
- [x] **After: wide shell live on prod at 1440px** — wordmark hard left, nav +
  Sign in + theme toggle hard right of the 78rem shell; hero spans the shell
  with the board's right edge on the nav anchor
  ([design/evidence/2026-07-11-after-1440px.png](design/evidence/2026-07-11-after-1440px.png)).
- [x] **Mobile unregressed at 390px**
  ([design/evidence/2026-07-11-after-390px.png](design/evidence/2026-07-11-after-390px.png));
  CSS budget green (~18.5KB of 32KB).
- [x] **Lighthouse on prod post-deploy**: performance **91**, accessibility
  **100**, best-practices **100**, SEO **100** — all ≥ 90 (h16).

## GitHub sign-in (c11/h1, c14/h4, c19/h21, c22/h22)

- [x] **OAuth app registered + secrets provisioned** — operator registered the
  GitHub OAuth app; `deploy.sh` mapped `.env` credentials into the stack
  (runbook: [runbooks/github-oauth-app.md](runbooks/github-oauth-app.md)).
- [x] **`/auth/login/github` redirects to GitHub with the client id and
  `read:user user:email` scopes, callback `https://league-of-agents.ai/auth/callback/github`**

  ```text
  GET /auth/login/github -> 302 github.com/login/oauth/authorize
      ?client_id=<present>&redirect_uri=...%2Fauth%2Fcallback%2Fgithub
      &scope=read%3Auser+user%3Aemail
  ```

- [x] **A real human signed in on prod and reached the signed-in state**
  (operator, 2026-07-11) — h1. The sign-in upserted a durable account record.
- [x] **GitHub is the only sign-in offered** — exactly one
  `/auth/login/*` link site-wide (`/auth/login/github`); no page links
  Google (h21).

## Browser play (c6/h19, c9/h20, c24/h24)

- [x] **The operator (a real human, real browser) signed in and played on
  prod** — h20. Feedback from that session ("Play a match routed to
  start-human", "no play path with a button") was fixed live: hero CTA +
  a Play nav entry now route to `/play`.
- [x] **Full match to completion through the play UI** (local instance of the
  same composed app, real `league` engine): signed-in session created a
  solo-vs-bot match via the `/play` form and submitted legal actions through
  the turn form until `FINISHED`; final view shows the score and the
  shareable `/matches/<id>/watch` replay link.

  ```text
  POST /play/matches (session)        -> 303 /play/matches/<id>
  30 form submissions of legal moves  -> each 303 back to the play view
  final view                          -> FINISHED, replay link present
  ```

- [x] **Signed-out `/play` invites sign-in** (prod): page renders the GitHub
  sign-in link, no play controls.

## Human-anchored agent tokens (c5/h18, c12/h2, c8/h12, c23/h23)

- [x] **Anonymous minting is refused on prod** — the identical call that
  returned 201 in the before-evidence now returns:

  ```text
  POST /auth/agents {"name":"after-evidence-probe-0711",...}
  -> HTTP 401 {"code": "authentication_required", "message": "minting an
     agent token requires signing in: a human must sign in at
     https://league-of-agents.ai and mint the token from their account
     (see https://league-of-agents.ai/start-agent)"}
  ```

- [x] **Session-minted token carries the owner** (local, same app): a
  signed-in session minted a token; the record stores `owner_account_id`;
  the agent then created a match and played a turn over the JSON API with
  that bearer token (201 / 200).
- [x] **Hard cutoff (c23)**: `verify()` refuses `owner_account_id=None`
  records with `401 anonymous_token` naming the onboarding path — covered by
  the both-stores cutoff tests; anonymous-era prod tokens can no longer
  authenticate (the mint that produced one is itself now impossible, above).

## Blocking (c13/h3, c10/h13, c16/h6)

Run on prod with a **disposable** account (`github:verify-chain-0711`) and
operator-issued owned tokens — the real account and its data were never
touched. Blocking/unblocking used the operator CLI against the live
DynamoDB table; no deploy, no restart.

- [x] **Agent under the account plays normally**: create 201, turn 200.
- [x] **Account block → 403 on the very next request**

  ```text
  league-site accounts block github:verify-chain-0711
  POST /api/v1/matches/<id>/turns (bearer)
  -> HTTP 403 {"code": "blocked", "message": "this credential is blocked"}
  ```

- [x] **Account unblock → next request 200.**
- [x] **Token-level block → 403; unblock → 200** (second disposable token,
  same account — the granularity is independent, h13).
- [x] **Revoked token is refused** on the participant endpoint (h6);
  disposable tokens revoked as cleanup.

## Incident found and fixed during this verification

- [x] **Shared-table scan crash (prod, found live)**: the tokens store's
  `list_all` scanned the whole DynamoDB table unfiltered; the account
  records created by real sign-ins made it crash (`KeyError: 'token_id'`),
  breaking minting guards and the tokens CLI. Root cause: no test covered
  the *shared* table with mixed item types. Fixed (PK-prefix filter,
  server-side + client-side), regression test added
  (`test_dynamodb_token_store_list_all_ignores_non_token_items_in_the_shared_table`),
  hotfix deployed and re-verified live the same hour.

## Boundaries (c18/h14)

- [x] **No billing/payment code in the diff** — the only
  monetization-adjacent artifact is the account-ownership substrate;
  reviewer-checkable on the PR.
