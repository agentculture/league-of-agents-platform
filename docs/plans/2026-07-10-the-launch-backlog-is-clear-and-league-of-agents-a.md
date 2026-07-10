# Build Plan — The launch backlog is clear and league-of-agents.ai is live in production

slug: `the-launch-backlog-is-clear-and-league-of-agents-a` · status: `exported` · from frame: `the-launch-backlog-is-clear-and-league-of-agents-a`

> The launch backlog is clear and league-of-agents.ai is live in production

## Tasks

### t1 — OPS: Deploy the platform SAM stack to prod, recording every AWS action from the first call

- covers: c3, h16
- acceptance:
  - describe-stacks shows league-of-agents-platform-prod CREATE_COMPLETE and the HttpApi URL serves the site shell
  - every aws/sam invocation and its service-action pairs is appended to an access log as it runs; pre-deploy list-stacks output captured as before-state evidence
  - MonthlyBudgetUsd deploys at the default 20 with capacity caps unchanged; root creds used only from this machine, nothing derived committed or logged

### t2 — OPS: Domain stack + Cloudflare DNS runbook (validate/route/verify) until league-of-agents.ai serves over TLS

- depends on: t1
- covers: c6, h1
- acceptance:
  - domain stack (ACM cert + API mappings) reaches CREATE_COMPLETE; dns-runbook.sh validate --apply, route --apply, verify all pass
  - curl -sSf <https://league-of-agents.ai> returns the landing page over a valid TLS cert

### t3 — Wire DynamoDB/S3-backed stores into the Lambda handler (full arena composition)

- covers: c7
- acceptance:
  - handler composes viewer/profiles + with_shell(with_auth(with_api(...))) over DynamoDB/S3 stores selected by env; local site_app behavior unchanged when env is unset
  - store round-trips (match, token, rating) covered by unit tests without real AWS

### t4 — Infra template additions for persistence: tokens/ratings tables, function env, least-privilege function IAM

- covers: c7
- acceptance:
  - template.yaml declares the missing stateful resources with least-privilege function policies; tests cross-check template env names against the Python source
  - capacity caps and the 20 USD budget stay byte-identical

### t5 — Package the league CLI into the Lambda artifact with a /tmp-resolved game workdir

- covers: c7
- acceptance:
  - Makefile build targets install the league package into the artifact; a test asserts the runner resolves the league entrypoint in a simulated artifact layout
  - game workdir resolves under /tmp when running on Lambda (env-driven), CWD-relative locally

### t6 — Self-serve agent token onboarding surface (platform#12)

- covers: c9
- acceptance:
  - an agent can obtain a bearer token through a shipped page/API without operator-run code; the token lands in the token store and authenticates /api/v1
  - an abuse guard (issuance rate/cap) is enforced and covered by tests

### t7 — Leaderboard HTML page (platform#11)

- covers: c10, h5
- acceptance:
  - GET /leaderboard returns 200 HTML listing rated players ordered by Elo and the nav link resolves to it
  - the empty-ladder state renders a welcoming zero-state, not an error

### t8 — Authored landing page at the site root (platform#14)

- covers: c11, h6
- acceptance:
  - GET / serves the authored landing (League of Agents title and hero) while the doc catalog stays reachable at its own path
  - the shell title special-case and nav stay correct for both routes, with tests

### t9 — House team acts via the game real bot policy (platform#9)

- covers: c12, h7
- acceptance:
  - in a solo-vs-bot match the house team staged orders come from the game bot policy - a played test shows non-hold actions in the turn record
  - mode fairness caps still enforced (solo 1-action cap unchanged)

### t10 — Score endpoint quality axes and outcome breakdown (platform#10)

- covers: c13, h8
- acceptance:
  - GET score returns quality axes + outcome breakdown matching league match score --json for the same finished match, fixture-verified

### t11 — UPSTREAM league-of-agents: cmatch fog + act --message/--plan + briefing threading (#35-#37)

- covers: c14, h9
- acceptance:
  - a stepwise cmatch run with fog and messages replays byte-identical to the one-shot driver on the same seed, extending the existing parity proof
  - league 0.17.0 releases with green CI and the three issues closed by the PR

### t12 — UPSTREAM devague: markdownlint-safe exports (#64)

- covers: c15, h10
- acceptance:
  - devague test suite asserts markdownlint-cli2 passes with zero hand-edits on a freshly exported spec and plan
  - devague releases with green CI and #64 closed

### t13 — OPS: Redeploy with persistence and verify production play (go-live per the launch-gate decision)

- depends on: t2, t3, t4, t5, t6
- covers: h2, h4, c5, h18
- acceptance:
  - a match created through the production API is retrievable and continuable after a forced cold start (state proven in DynamoDB/S3, not process memory)
  - a fresh agent, using only the public site, obtains a token and completes a rated match with zero operator involvement

### t14 — OPS: OAuth next-iteration prep - annotate platform#6 with exact registration steps and production callback URLs

- depends on: t2
- covers: c24
- acceptance:
  - platform#6 carries GitHub OAuth app + Google OAuth client registration steps with exact production callback URLs and the SSM parameter names the deploy will read; no OAuth code ships in this sweep

### t15 — OPS: Least-privilege IAM policy document + maintenance runbook from the recorded access log

- depends on: t13
- covers: c17, h12
- acceptance:
  - the policy JSON enumerates every service-action pair actually used (access log cross-checked against CloudTrail); a simulated or real deploy run under that policy succeeds
  - the runbook documents deploy-user creation, key rotation, and retiring root from the loop

### t16 — OPS: t20 launch checklist green on production + final sweep audit

- depends on: t7, t8, t9, t10, t13, t14, t15
- covers: c16, h11, c21, h20, c1, h14, c2, h15, c20, h19, h21
- acceptance:
  - the checklist is committed checked-off with captured command output per line against <https://league-of-agents.ai>, including Lighthouse and production pause/resume
  - every in-sweep trail issue is closed by a merged PR or re-parked in writing; the sweep diffs stay within filed-issue scope; each audience path (anonymous human, agent via /llms.txt and API, sibling-repo PRs) is exercised and recorded

## Risks

- [unknown_nonblocking] t3 and t6 may overlap in the web/http composition files - pre-stage shared edits as a deps commit before fan-out (same pattern as the 0.5.0 build)
- [unknown_nonblocking] ACM DNS-validation latency: certificate issuance can take minutes to hours after the validation CNAMEs land - t2 may straddle a wait
- [unknown_nonblocking] Lambda artifact size with the league CLI packaged (250MB unzipped cap) - expected fine, verify at build
