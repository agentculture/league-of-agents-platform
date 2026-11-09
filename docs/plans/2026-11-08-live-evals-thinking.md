# Build Plan — live evals + thinking

slug: `live-evals-thinking` · status: `exported` · from frame: `live-evals-thinking`

> league-of-agents.ai/evals/ shows model-vs-model matches live, turn by turn, with each model's thinking alongside the board — redesigned, Playwright-verified, and exercised by a pi agent on Nemotron Super

## Tasks

### t1 — Evals data schema: `league_site`/evals/schema.py defines the versioned per-match file (state, per-side fog, plans, messages, actions, rejected, latency, think, slices, metrics, structured status+reason, per-seat tool/network setting), the per-eval manifest, thinking and in-progress seat files

- covers: c2, h7, c35, c7, h12
- acceptance:
  - pytest: the prototype data.json, converted per match, validates; a file missing status or reason fails validation
  - pytest: schema exposes a major version and a function that says whether a version is supported
  - t1 alone creates `league_site`/evals/`__init__.py`; t2 and t9 add their modules without touching it

### t2 — Free-text scanner: `league_site`/evals/scan.py classifies reasoning and chat text as clean or not (secret-like strings, hostnames, IPv4/IPv6, absolute paths, emails), failing closed and reporting file, seat and pattern

- covers: c3, h8, c25, h22
- acceptance:
  - pytest: planted API key, hostname, private IP, home path and email inside text are each caught; ordinary game text with coordinates like \[10,8\] passes
  - pytest: an exception or unknown input type is treated as not clean
  - a scan report over the 45 prototype thinking files is produced and attached to the PR, listing every hit

### t3 — Slicer: `league_site`/evals/slicer.py turns a match's turns into key-moment slices and templated headlines, plus per-match findings that cite their turns

- depends on: t1
- covers: c12, h2, c17, h3, c18, h4, c19, h19
- acceptance:
  - pytest: hard-a yields slices at turns 17, 18 and 23 with the expected headlines and score deltas
  - pytest: running the slicer twice on the same input gives identical output; no model client is imported
  - pytest: every finding carries the turn that backs it

### t4 — Benchmark metrics: `league_site`/evals/metrics.py computes per side per match chat words per turn, median answer time, illegal-order rate, thinking tokens, share of unit-turns in the enemy half, contact within 2 cells, points per turn, order mix and thinking tokens per turn, each with a one-line definition

- depends on: t1
- covers: c21, h5
- acceptance:
  - pytest: values for thief-a4 match a hand-checked fixture (e.g. GLM 1 rejected of 144, Sonnet 16 of 144)
  - pytest: every metric exposes its definition string, and nothing reads outside the match data

### t5 — Exporter: `league_site`/evals/export.py folds `.league/matches/<id>` logs and match config into per-match files, manifest and thinking files; runs the scanner and drops failing files only; embeds slices and metrics; refuses to publish private content live for a match where any seat has tools or network on

- depends on: t1, t2, t3, t4
- covers: c8, h13, c29, h26, c34
- acceptance:
  - pytest: a fixture match exports files that validate against the schema; a thinking file with a planted hostname is left out while the turn still exports
  - pytest: a config with a tools-on seat exports boards and scores but no live chat, plans or reasoning
  - pytest: the manifest is under 5 KB for six matches and carries per-match ETags
  - the exporter reads only exported match files; no engine or harness module is imported

### t6 — Infra: a dedicated evals bucket in infra/template.yaml with public read only under the evals prefix and no lifecycle rule that deletes or tiers it, plus a match-host IAM user/policy (new infra/iam-match-host-policy.json) limited to put/delete under that prefix

- covers: c27, h24, c28, h25
- acceptance:
  - pytest template test: the evals bucket is separate from ArchiveBucket, its policy grants public GetObject only on the evals prefix, and it has no expiring lifecycle
  - pytest: the match-host policy allows only s3:PutObject and s3:DeleteObject on the evals prefix

### t7 — Cloudflare: route /evals/data/\* to the evals bucket on the same domain with cache rules (running match files max-age 30 s or less, finished files immutable), and add an unpublish runbook (delete, purge, confirm 404) to docs/runbooks/cloudflare-league-of-agents-ai.md

- depends on: t6
- covers: c26, h23, c33, h30
- acceptance:
  - curl -I through Cloudflare on a test object shows the expected cache headers and a fresh age after re-upload
  - the unpublish runbook is exercised once on a test object and the 404 is recorded in the PR

### t8 — Publisher: league-site evals publish (CLI under `league_site`/cli) uploads with the match-host credential: thinking files first, data and manifest last, with cache headers; a --stream mode uploads each answering seat's in-progress thinking and response about every 10 s after scanning each chunk

- depends on: t5, t6
- covers: c31, h28, c37, h33
- acceptance:
  - pytest with a fake S3: upload order is thinking, then match file, then manifest; a reader between steps never sees a missing referenced file
  - pytest: a chunk that fails the scanner is never uploaded; cache headers are set per file type
  - dry-run is the default and prints the upload plan as JSON, per the CLI conventions

### t9 — Evals styles: `league_site`/evals/styles.py builds the page CSS from theme.py tokens only, with the validated team colours (light #2b54c4/#b4314f, dark #6f8be6/#e0627f) added as tokens in `league_site`/web/theme.py

- covers: c6, h11
- acceptance:
  - pytest: evals CSS contains no raw hex values outside the token block
  - the dataviz palette validator passes for the team colours in light and dark; output attached to the PR

### t10 — Evals routes: `league_site`/evals/wsgi.py serves `/evals/` and `/evals/<eval>/<match>/` inside the site shell and mounts into the site app

- depends on: t9
- covers: c13, h15
- acceptance:
  - pytest: both routes return 200 with the shell, labelled controls and a noscript note; unknown ids return 404
  - t10 creates `league_site`/evals/`replay_js.py` and `charts_js.py` as stub constants already wired into the page, so t11 and t12 each edit only their own file

### t11 — Replay script: `league_site`/evals/`replay_js.py` renders the sticky board, the slice story beside it, the open moment (seats, orders, chat, reasoning on demand), the asymmetry note, live polling of the manifest, in-progress streaming, the stale badge and the schema-version guard

- depends on: t1, t10
- covers: c4, h9, c5, h10, c11, h1, c16, h18, c32, h29, h31, h32, c15, h17
- acceptance:
  - node harness: an at-end viewer advances when the manifest gains a turn, a scrubbed viewer does not; in-progress files are polled only at the latest turn during a live turn
  - node harness: status running with data 2 h old shows the no-update message; an unsupported schema version shows the version message
  - node harness: a Claude seat shows the claude -p note and a GLM seat loads reasoning text

### t12 — Benchmarks script: `league_site`/evals/`charts_js.py` renders the index benchmarks (tiles, aggression vs effectiveness, order mix, per-match small multiples, thinking per turn) from exported metrics, with tooltips, keyboard access, table views and each metric's definition

- depends on: t1, t10
- covers: c14, h16
- acceptance:
  - node harness: each chart has a table view whose numbers equal the exported metrics
  - node harness: tooltip text is set with textContent and every mark has a hit target of at least 24 px

### t13 — Playwright suite for /evals/ in tests/e2e plus a CI job: 1440 and 390 px in light and dark, keyboard turn stepping, labelled controls, live advance with a mocked manifest, reasoning loaded on demand; screenshots saved as evidence

- depends on: t11, t12
- covers: c10, h14
- acceptance:
  - CI runs the suite against a local site serve with fixture data and it passes
  - screenshots at both widths and both themes land in docs/design/evidence/

### t14 — pi + Nemotron tester: scripts/pi-tester.sh runs pi on lobes 'worker' (Nemotron 3.5 Super) against a local site serve to exercise /evals/ and the agent onboarding and play flows, plus read-only checks of production /evals/; the run log is kept as evidence

- depends on: t11, t12
- covers: c30, h27
- acceptance:
  - the run completes and its log is committed under docs/verification/; production ratings and leaderboard are unchanged (before/after diff)

### t15 — Engine brief: file an issue on agentculture/league-of-agents (communicate skill) for in-progress reasoning and response output per seat, per-seat tool and network settings in match config, and a pi command driver for Nemotron run with --no-tools --no-mcp

- covers: c37
- acceptance:
  - the issue exists, cites this spec, and lists the three asks with acceptance criteria

### t16 — Verify and ship: deploy, smoke-test /evals/ during a running match, run the two cold reads of hard-a, the evaluator and visitor hallway check, keep the prototype before-screenshot, bump the version and CHANGELOG

- depends on: t8, t7, t13, t14, t15
- covers: c1, h6, c20, h20
- acceptance:
  - production /evals/ shows a live match advancing and streaming reasoning, recorded in the PR
  - two cold readers both name hard-a turns 17, 18 and 23; answers recorded
  - version bumped and CHANGELOG entry added

## Risks

- [unknown_nonblocking] Wave 0 has three tasks creating modules in the new `league_site`/evals package; only t1 may create `__init__.py`, or the merges collide (task t1)
- [unknown_nonblocking] The pi tester and the live streaming depend on engine-repo work (issue from t15): in-progress seat output and per-seat tool settings; if it lands late, t8's --stream mode and t5's tools check run against fixtures only (task t8)
- [unknown_nonblocking] Whether pi's print mode returns Nemotron's full reasoning text through the lobes gateway is unverified; t14 records what it actually gets (task t14)
- [unknown_nonblocking] The Cloudflare origin rule to an S3 bucket may need a Worker if the plan tier lacks origin rules; t7 decides and records which (task t7)
