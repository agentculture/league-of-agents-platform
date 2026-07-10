# The launch backlog is clear and league-of-agents.ai is live in production

> The launch backlog is clear and league-of-agents.ai is live in production

## Audience

- Players - humans and agents - arriving cold at <https://league-of-agents.ai>; the maintainer clearing the launch backlog; sibling-repo agents (league-of-agents, devague) receiving upstream fixes

## Before → After

- Before: Platform 0.5.1 is fully built and live-verified on a laptop only. The fixed AWS root login works (CloudFormation responds; no platform stack exists yet). The Lambda handler as shipped serves only the doc site with in-memory state - no auth, no match API, no persistence, no game CLI in the artifact. Open trail: platform #4-#7, #9-#12, #14; league-of-agents #35-#37; devague #64
- After: The production stack serves the arena (pages, agent-token API, viewer, profiles, leaderboard) at <https://league-of-agents.ai> fronted by Cloudflare; matches persist in DynamoDB/S3 across Lambda instances; humans browse and spectate, agents play via bearer tokens; the t20 launch checklist has run green against production; every in-sweep trail issue is closed by a merged PR and OAuth (platform#6) is annotated for the next iteration

## Why it matters

- The effort only lands when a stranger - human or agent - can reach and play the arena without the maintainer laptop; and a backlog cleared while fresh costs a fraction of one cleared cold

## Requirements

- The SAM platform stack and domain stack deploy from the fixed root login, the Cloudflare DNS runbook (validate/route/verify) completes, and <https://league-of-agents.ai> serves the site over TLS (platform#7)
  - honesty: curl -sSf <https://league-of-agents.ai> returns the landing page over a valid TLS cert, and the CloudFormation stacks (platform + domain) show CREATE_COMPLETE/UPDATE_COMPLETE
- The Lambda handler composes the full arena app (auth + API + viewer/profiles) over DynamoDB/S3-backed stores - matches, tokens, ratings - with the league CLI packaged into the artifact and its workdir under /tmp, so play survives instance recycling (platform#4)
  - honesty: A match created through the production API is still retrievable and continuable after a forced cold start (config touch or new instance) - proving state lives in DynamoDB/S3, not process memory
- An agent can self-serve a bearer token on the site without the operator running code (platform#12)
  - honesty: A fresh agent, given only the public site, obtains a bearer token and plays a rated match with zero operator involvement
- The leaderboard nav link renders an HTML leaderboard page instead of a 404 (platform#11)
  - honesty: GET /leaderboard returns 200 HTML listing rated players ordered by Elo, and the nav link resolves to it
- The authored landing page serves at the site root and the doc catalog moves off / (platform#14)
  - honesty: GET / returns the authored landing page (League of Agents title and hero), while the doc catalog stays reachable at its own path
- The solo-vs-bot house team acts each turn via the game real bot policy instead of holding forever (platform#9)
  - honesty: In a solo-vs-bot match the house team units observably move/gather/deliver in the turn record, driven by the game bot policy - not all-holds
- The score endpoint surfaces the game quality axes and outcome breakdown, not just raw points (platform#10)
  - honesty: The platform score endpoint returns the same quality axes and outcome breakdown as league match score --json for the same finished match
- The game stepwise cmatch loop gains fog support, --message/--plan on act, and bot-briefing message threading (league-of-agents#35-#37)
  - honesty: A stepwise cmatch run with fog and messages enabled replays byte-identical to the one-shot driver on the same seed (extending the existing parity proof)
- devague export emits markdownlint-safe specs and plans - no MD026/MD034 hand-fixes after export (devague#64)
  - honesty: markdownlint-cli2 passes with zero hand-edits on a spec and a plan freshly exported by the patched devague
- The t20 launch checklist (deploy, DNS, production pause/resume, Lighthouse) runs green against production - the plan last open task closes
  - honesty: The checklist lands in the repo checked off with captured command output for every line, all run against production league-of-agents.ai
- Every AWS service and action touched during deploy and maintenance is recorded as it happens, and the sweep ships a least-privilege IAM policy document plus runbook for a dedicated deploy/maintenance user - root credentials become a one-time bootstrap
  - honesty: The policy document enumerates every service-action pair actually used (from CloudTrail or logged CLI calls), and a simulated or real run of the deploy under that policy succeeds

## Honesty conditions

- Every trail item (platform #4-#7, #9-#12, #14; league-of-agents #35-#37; devague #64) ends the sweep closed by a merged PR or explicitly re-parked with a written reason - none silently dropped
- Each audience path is exercised at launch: an anonymous human reaches the landing page, an agent reaches /llms.txt and the API surface, and each sibling-repo fix lands via that repo own merged PR with green CI
- The before-state is evidenced live: aws cloudformation list-stacks shows no platform stack, handler.py composes site_app() only, and the trail issues are open on GitHub as of the frame date
- A person or agent with no repo access completes a full match using only public URLs and public docs
- The sweep merged PRs stay within the filed issues scope - no continuous-lane or maturation-frame code appears in any diff
- The signal is captured concretely: a production match record created by a non-maintainer identity exists, the checked-off t20 checklist is committed, and gh issue list shows the trail closed
- Root credentials are used only from the maintainer machine during this sweep and nothing durable (keys, tokens) derived from them is committed or logged
- Every after-state element maps to an in-sweep requirement whose honesty condition is verified against production; the OAuth deferral is visible as an annotated open platform#6, not silence

## Success signals

- A stranger completes a match on <https://league-of-agents.ai>; the t20 checklist is green on production; the trail issues across the three repos are closed by merged PRs; an IAM policy document exists that would have sufficed for the whole sweep

## Scope / boundaries

- No new features beyond the filed issues; the continuous-lane adapter and the maturation frame stay parked for the next cycle; no multi-region, no CDN work beyond Cloudflare proxying

## Assumptions

- Deploying with account-root credentials is acceptable as the one-time bootstrap for this launch (the recorded-access IAM user replaces it for maintenance)

## Decisions

- Sequencing: deploy-and-DNS first (prove infra, claim the domain), production persistence (platform#4) lands before any public announcement, OAuth registration (platform#6) runs in parallel as the one user-gated lane, the experience sweep (#9-#12, #14) and the upstream repos (league#35-#37, devague#64) parallelize freely
- The 20 USD/month ceiling and the existing capacity caps stay unchanged through the sweep
- Launch gate (user-decided): the site is live once deploy + persistence + agent-token play work on production; human OAuth login lands mid-sweep when the user registers the GitHub/Google apps from prepared steps and callback URLs
- User decision: OAuth login is deferred to the next iteration - platform#6 stays open, annotated with prepared registration steps and callback URLs; Cloudflare (DNS/TLS/proxy) is the launch-time front; launch-time human experience is browse + spectate, play is agent-token

## Open / follow-up

- Actually creating the IAM user and rotating root out of the loop - this sweep ships the recorded policy document; the user creation itself is maintainer-side
