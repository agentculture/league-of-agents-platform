# live evals + thinking

> league-of-agents.ai/evals/ shows model-vs-model matches live, turn by turn, with each model's thinking alongside the board — redesigned, Playwright-verified, and exercised by a pi agent on Nemotron Super
> instruction: Smoke-test the deployed /evals/ during a running match; attach Playwright screenshots under docs/design/evidence/; link the pi run log in the PR

## Audience

- Agent builders and model evaluators comparing how models play, plus curious visitors following a live match
  - instruction: Hallway check with one evaluator and one non-technical reader; note what each could and could not answer

## Before → After

- Before: The prototype replay shows the board, then a score chart, then a full log far below it: you scrub a slider and read a wall of turn records to work out what happened
  - instruction: Keep a screenshot of the prototype as the before evidence in docs/design/evidence/
- After: A reader opens a replay and reads the match as a short story of key moments, each beside its board, and can open any moment to read the orders, chat and GLM reasoning behind it
  - instruction: Playwright: click a slice, assert the board is in view and the seats, chat and reasoning panels render for that turn

## Why it matters

- A benchmark result is only credible if a reader can see why it happened: which moments decided the match and what each model was thinking at that moment
  - instruction: Check that findings, slices and benchmark marks all link or drill down to a turn or table row

## Requirements

- A data export contract for evals: the prototype builder's data.json (per turn: state, per-side fog vis/seen bitstrings, plans, messages, actions, rejected, latency, think token counts) becomes a versioned schema under `league_site`/datasets, alongside schema.py/export.py
  - instruction: Add an evals schema next to datasets/schema.py with a version field; unit test round-trips the prototype data.json through it
  - honesty: One versioned schema (`league_site`/datasets) describes data.json and the thinking files; the page and the exporter both validate against it
- GLM reasoning is fetched on demand per seat per turn and runs through `league_site`/datasets/scrub.py plus a hostname/IP/path check; a failed scrub drops that file, not the turn
  - instruction: Extend scrub.py with network-identifier patterns; test with planted hostnames/IPs; test that a failing file is dropped and data.json still exports
  - honesty: No published thinking file contains a hostname, IP address, internal path or credential, and a file that fails the scrub is left out while the turn still publishes
- The replay page labels the reasoning asymmetry: GLM full text, Claude seats (claude -p) token counts only — think\[\].`has_text`=false and `completion_tokens`=null for red seats in the data
  - instruction: Playwright check: a Claude seat shows the claude -p note; a GLM seat shows reasoning text
  - honesty: A reader can tell from the page alone which seats have full reasoning and which have none, and why
- Live = fresh static files: the page polls data.json about once a minute, advances only when the viewer sits at the latest turn, and shows a live badge with turn N of limit and updated time
  - instruction: Playwright test with a mocked data.json that gains a turn: at-end viewer advances, scrubbed viewer stays
  - honesty: A viewer at the latest turn sees a new turn within about a minute of the export landing, and a viewer scrubbed back is never moved
- The redesign reuses the dawn-arena theme tokens from `league_site`/web/theme.py (Fraunces + Albert Sans, aurora teal accent, light/dark) instead of the prototype's own palette and Chakra Petch fonts
  - instruction: grep evals CSS for raw hex outside theme tokens; screenshot light and dark; compare against the dawn-arena direction doc
  - honesty: The evals pages use only theme.py tokens and the family fonts, and pass the family-kinship read in light and dark
- Each replay header and the index carry the fairness log: void/tie/live status and the harness-bug reason per match (thief-a void, a4 tie, a5 tie, a6 live, opus-a queued)
  - instruction: Schema has status + reason fields; test that thief-a renders as void with its reason
  - honesty: Every stopped, void or tie match carries a structured status and reason in the export, shown in its header and the index
- Playwright checks the evals pages at 1440 and 390 px in light and dark: keyboard scrubbing, labelled controls (#36), live-advance not stealing a scrubbing viewer, and the thinking drawer loading on demand
  - instruction: Add a Playwright suite for /evals/ to tests and the CI workflow; store screenshots as evidence
  - honesty: Playwright covers 1440 and 390 px in light and dark, keyboard turn stepping, labelled controls, live advance, and on-demand reasoning loading, and runs in CI
- Gameplay sits next to the board it explains: plans, chat, orders and reasoning for a moment are shown beside that moment's board, never in a log section below it
  - honesty: At every width the selected moment's talk is visible together with its board, with no scrolling between them (sticky board on desktop, sticky compact board on phone)
- The game is read in slices: the replay breaks a match into key moments, each a board snapshot plus the information drawn from it (what changed, who scored, what the seats said and reasoned about it)
  - honesty: Every slice cites the turn it came from and opens to that turn's full record; nothing on a slice comes from anywhere but the exported data
- Slices are derived deterministically from the turn data, not written by hand: a turn becomes a slice when the score changes, a point is captured, a mission completes, a steal happens, a Crown streak starts or ends, the lead changes, or orders are rejected or lost; quiet turns fold into the preceding slice
  - honesty: Running the slicer twice on the same data.json gives identical slices; it is a pure function of the export
- Each eval shows a few findings drawn from the slices with their counts, such as rejected orders per side, lost orders, and turns where both sides scored the same on the mirrored board
  - honesty: Each finding links to the slices that back it, so a reader can check the count
- The evals index carries a benchmarks section computed from the export: chat words per turn, answer time, illegal-order rate and thinking tokens per side; aggression (share of unit-turns in the enemy half, contact within 2 cells) against effectiveness (points per turn); order mix by type; per-match small multiples; GLM thinking tokens per turn. Every chart has hover tooltips, keyboard access and a table view
  - honesty: Each metric is defined in one place, computed only from data.json, and its definition is stated on the page; team colours pass the dataviz palette validator in light and dark
- The reasoning and chat scanner fails closed: any text it cannot classify as clean (secret-like strings, hostnames, IPs, absolute paths, emails) keeps that file out of the publish and logs which pattern hit
  - honesty: Running the scanner over all 45 existing thinking files reports every hit with file, seat and pattern, and nothing with a hit is uploaded
- Live files are served with explicit cache headers: a running match's data.json at max-age of 30 s or less, finished matches and thinking files immutable; the prior theme.css incident shows Cloudflare holding origin files for 4 hours
  - honesty: curl -I on a running match's data.json through Cloudflare shows the short max-age and a fresh age after each upload
- The match host uploads with a dedicated credential that can only put and delete objects under the evals prefix; it is not the deploy identity (issue #39 notes deploys still run as account root)
  - honesty: An attempt with the match-host credential to write outside the evals prefix, or to any other bucket, is denied
- A player seat never has a way to read the live site: harness seats run with tools and network off, and a pi-driven Nemotron seat runs pi with --no-tools and --no-mcp; publishing team chat and reasoning live is only allowed for matches where every seat meets this
  - honesty: The harness records each seat's tool and network setting in the match config, and the exporter refuses to publish private content live for a match where any seat has tools on
- The match host uploads a turn's thinking files first and its data.json last, so a reader never sees a turn whose reasoning files are not yet there; a missing file still renders as not published
  - honesty: A test that reads between the two upload steps never finds data.json referencing a thinking file that is absent
- The live badge goes stale honestly: if a running match's data has not changed for 60 minutes (about twice the slowest turn), the page says no update since HH:MM UTC instead of Live
  - honesty: With a mocked data.json whose generated time is 2 hours old and status running, the page shows the no-update message, not Live
- A tested unpublish path exists: delete the object from the evals bucket, purge it from Cloudflare, and confirm a 404 through the public URL, written up as a runbook beside the existing cache-purge section
  - honesty: The runbook is exercised once on a test object and the 404 is recorded
- Polling is cheap: the page polls a small per-eval manifest (latest turn, generated time, per-match ETag) and downloads a match's data only when it changed; each match has its own file instead of the prototype's single 2.4 MB bundle of all matches
  - honesty: A viewer idle at the latest turn for 10 minutes downloads under 50 KB when no turn lands
- The page reads the data schema version and shows a clear message for a version it does not support, since the page ships with the Lambda package and the data ships separately from the match host
  - honesty: Serving data with a future major version shows the unsupported-version message and no broken board
- Streaming stays within static files: while a seat is answering, the match host writes that seat's in-progress thinking and response to a per-seat file every 10 s or so, scanned before each upload; the page polls it every 10 s only while a turn is in progress and the viewer is at the latest turn, and drops it when the turn resolves. Claude seats stream their response text only, since claude -p returns no thinking
  - honesty: During a live turn, a viewer at the latest turn sees a GLM seat's thinking grow within about 10 s of it being generated, a viewer scrubbed back gets no in-progress polling, and a chunk that fails the scanner is never uploaded

## Honesty conditions

- /evals/ on league-of-agents.ai shows a live match advancing turn by turn, its seats' reasoning, Playwright evidence at 1440 and 390 px, and a recorded pi + Nemotron Super run
- No harness or engine code is copied into this repo; the platform imports only exported files
- The page answers both audiences: an evaluator can compare the models from the benchmarks section, a visitor can follow the live match from the story
- The before state is the prototype artifact as of 2026-11-08 (board, chart, then log below)
- Each headline result on the page links to the moments and data that produced it
- From any replay, a reader can open any moment and see its orders, chat and GLM reasoning without leaving the board
- No text on the evals pages is generated by a model at build or view time; headlines come from templates over events
- Two people who have not seen hard-a both name turns 17, 18 and 23 as deciding moments from the story alone
- A planted API key, hostname, private IP and home path inside a reasoning string are each caught by the new scanner in a unit test
- infra/template.yaml shows a separate evals bucket with its own public-read policy scoped to the evals prefix and no lifecycle rule that deletes or tiers published evals
- After a tester run, the public leaderboard and every rated player's Elo are unchanged

## Success signals

- Someone who has not seen a match can say from the story alone which turns decided it and why, for example hard-a turns 17, 18 and 23, without opening the turn log
  - instruction: Run the cold read with two people and record their answers in the PR

## Scope / boundaries

- The engine and harness stay in agentculture/league-of-agents; the platform only consumes exported logs — no harness code is vendored here
  - instruction: Review the PR diff: no files from agentculture/league-of-agents are vendored; the exporter reads .league/matches output only
- Slice headlines are templated from events (team, action, object, points); no LLM writes or summarises match narrative on the site
  - instruction: Code review: slice headlines come only from sliceHeadline-style templates; no LLM client in the build path
- Evals data lives in its own bucket, never in ArchiveBucket: the archive bucket blocks public policy and its bucket-wide lifecycle moves objects to cheaper tiers at 30 and 90 days, while cleanup.py deletes only under archives/
- The pi + Nemotron tester agent never changes public standings: its matches are unrated and hidden from the leaderboard (ratings ledger `record_match`), or it runs against a local site serve; its runs count against MaxConcurrentMatches only when explicitly allowed

## Non-goals

- No live websocket or per-turn API: matches resolve every 15-30 minutes, so polling static JSON is sufficient

## Assumptions

- scrub.py can clean free-text reasoning — FALSE as built: it denies values under field names matching key/token/secret/password/credential and does not scan free text, so the reasoning scanner is new code

## Scope exploration

- `s1` — `prototype artifact data.json (2.4 MB, 6 matches)`: turn records carry state/fog/plans/messages/actions/rejected/latency/think/events; thinking text lives in separate `thinking/<tag>-t<N>.json` files (~80-640 KB each)
  - seeds: `c2`
- `s2` — `league_site/datasets/scrub.py`: existing scrubber is the reuse point issue 43 names; thinking text is free text and needs the extra network-identifier pass
  - seeds: `c3`
- `s3` — `data.json think[] records`: red (claude -p) seats have `completion_tokens` null and `has_text` false; blue GLM seats carry `reasoning_tokens` and text
  - seeds: `c4`
- `s4` — `issue 43 update §1`: live replaces the original static-only proposal; no match API or DynamoDB in the read path
  - seeds: `c5`
- `s5` — `league_site/web/theme.py + docs/design/dazzle-direction.md`: theme.py is the single token source; family-kinship bar (c21/h16) applies to every page; prototype uses an unrelated palette
  - seeds: `c6`
- `s6` — `issue 43 update §3 + data.json labels`: stop reasons currently live only inside free-text labels; they need structured status+reason fields
  - seeds: `c7`
- `s7` — `~/git/league-of-agents/league/harness.py`: harness drivers (bot, bot-file, command, resident) and session audit logs live in the engine repo; the platform renders their exports
  - seeds: `c8`
- `s8` — `issue 43 update §1 cadence`: turns resolve every 15-30 min; push transport adds cost against the 20 USD/month ceiling (docs/capacity.md) with no viewer benefit
  - seeds: `c9`
- `s9` — `docs/design/evidence/*.png`: prior design passes recorded before/after screenshots at 1440 and 390 px; the evals pass follows the same evidence convention
  - seeds: `c10`
- `s10` — `infra/template.yaml`: only S3 bucket is the private ArchiveBucket for cleanup; static assets are served from the Lambda package (`league_site`/web/assets), so per-turn upload needs a new public prefix or a Lambda proxy
- `s11` — `~/.pi/agent/models.json (pi 1.5.0)`: pi is configured with the lobes gateway: model 'worker' = Nemotron 3.5 Super VL 120B (reasoning, 262k ctx), 'cortex' = GLM-5.3-Flash; Nemotron reasoning text would be capturable, unlike claude -p
- `s12` — `challenge pass / unstated-assumptions lens: league_site/datasets/scrub.py`: `_DENY_FIELD_PATTERN` matches field names only; free-text values are never scanned, so c3's 'runs through scrub.py' is insufficient on its own
  - seeds: `c24`
- `s13` — `challenge pass / adjacent-systems lens: docs/runbooks/cloudflare-league-of-agents-ai.md (Cache purge section)`: a past incident served stale theme.css for 4 h under max-age=14400 with cf-cache-status HIT; live JSON would go stale the same way without explicit headers
  - seeds: `c26`
- `s14` — `challenge pass / operations lens: infra/template.yaml ArchiveBucket + league_site/aws_lambda/cleanup.py`: ArchiveBucket has PublicAccessBlock (BlockPublicPolicy) and lifecycle transitions at 30/90 days; cleanup sweeps archives/ only
  - seeds: `c27`
- `s15` — `challenge pass / security lens: infra/iam-deploy-policy.json + issue #39`: deploy policy is broad and deploys run as root; a match host holding it could touch the whole stack
  - seeds: `c28`
- `s16` — `challenge pass / overlooked-actors lens: pi --help + issue 43 fairness rules`: pi ships read/bash/edit/write tools by default; a pi seat with tools could fetch /evals/ and read the opponent's live private chat; claude -p seats run tools off per issue 43
  - seeds: `c29`
- `s17` — `challenge pass / overlooked-actors lens: league_site/ratings/ledger.py record_match + docs/capacity.md caps`: matches played on production feed the ratings ledger and count toward MaxConcurrentMatches; a tester agent would pollute both
  - seeds: `c30`
- `s18` — `challenge pass / observability lens: issue 43 cadence (15-30 min per turn)`: nothing on the page or host detects a dead match host; the prototype badge would say live forever
  - seeds: `c32`
- `s19` — `challenge pass / reversibility lens: docs/runbooks/cloudflare-league-of-agents-ai.md`: the only purge procedure covers theme.css/site.js; published reasoning text has no takedown path
  - seeds: `c33`
- `s20` — `challenge pass / operations lens: prototype data.json (2.4 MB, all matches) + docs/capacity.md 20 USD ceiling`: polling a growing all-matches bundle every minute costs each viewer about 2.4 MB/min
  - seeds: `c34`
- `s21` — `challenge pass / concurrency lens: S3 single-object writes`: each S3 PUT replaces an object atomically for readers; the only cross-object race is thinking vs data.json, covered by the upload-order requirement
- `s22` — `challenge pass / unexamined: agentculture/league-of-agents harness internals for a pi driver`: did not read how a command driver would launch pi or capture Nemotron reasoning through the lobes gateway; that work lives in the engine repo

## Decisions

- Live turns reach /evals/ by upload to a new public S3 prefix (served under /evals/ via the existing Cloudflare front), written by the match host after each resolved turn; only finished matches are committed to the repo; the Lambda stays out of the read path
- pi with Nemotron 3.5 Super (lobes 'worker') is used two ways: as a new eval player, wired as a harness command driver in agentculture/league-of-agents with its reasoning text captured; and as an external tester agent that exercises /evals/ and the agent onboarding and play surface, its run log kept as evidence
- Private content streams live: while a match runs, viewers see each seat's thinking and responses as they are generated, not only after the turn resolves; this is allowed because every seat runs without tools or network access

## Hard questions

- Does streaming in-progress thinking at about 10 s keep 'no websocket' honest, or does it need a push transport after all?

## Open parks

- [unknown_nonblocking] Whether Nemotron reasoning via the lobes gateway returns full thinking text through pi's print mode, and in what shape, is unverified
- [unknown_nonblocking] Rendered page weight as reasoning files accumulate over a 40-turn match with 6 seats is unmeasured (about 220 KB per turn so far)
