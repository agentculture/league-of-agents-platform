# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/). This project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.8.1] - 2026-07-11

### Fixed

- Favicon rendered blank in browser tabs: the mark was a <text> \u2694 glyph and favicon renderers have no glyph for it in their serif fallbacks. The site now wears the family mesh mark (three nodes, two threads) verbatim from agentculture.org — pure vector geometry, renders everywhere, scheme-aware.

## [0.8.0] - 2026-07-11

### Added

- Full-adoption dawn design system: league-of-agents.ai now shares agentculture.org's dawn palette, sky-wash and mesh decorative tokens in both light and dark schemes, verified WCAG AA in both
- Self-hosted Fraunces Variable (display, SOFT 75/WONK 0) and Albert Sans Variable (body) fonts, vendored and served first-party at /fonts/*.woff2 with preloads
- The landing hero is now a strategy-game board: role-distinct units (scout/harvester/defender), gatherable resource nodes, capturable control posts, a real missions+control+resources score readout, and a message ticker, driven by a first-party JS sim engine that plays a varying 12-turn loop; JS-off/reduced-motion renders a legible poster-frame still
- Dawn-palette favicon (with its own prefers-color-scheme dark variant) and a refreshed og:image share card
- Versioned asset URLs: every stylesheet/script/font/favicon/og-image reference the shell emits now carries a content-hash query so a deploy can never strand a stale asset against new markup
- Cloudflare cache-purge runbook section (purge-by-URL procedure, required token scope, and when purging is/isn't needed once versioned URLs ship)

### Changed

- Renegotiated asset-budget contract: CSS 24KB->32KB, JS 8KB->16KB, and a new 320KB font allowance (368KB total first-party asset weight); zero external requests preserved
- Wordmark set mixed-case ("League of Agents") in the new Fraunces display voice, replacing the retired uppercase/mono scoreboard treatment
- Share-card (og:image) palette moved to the dawn identity

### Fixed

- Root-caused and fixed the production theme-toggle incident: Cloudflare's edge was serving a stale pre-dazzle /theme.css against current-build HTML; versioned asset URLs make that class of staleness structurally impossible going forward

## [0.7.2] - 2026-07-11

### Added

- Converged devague spec + 10-task plan for the agentculture.org family alignment (full adoption: dawn palette, self-hosted Fraunces + Albert Sans, mesh motif, toggle kept) and the strategy-game demo board (JS-driven sim: unit roles, resource gather/deliver, capturable control posts, message ticker, real scoring formula)

### Fixed

- Root-caused the dead theme toggle on production: Cloudflare edge serves the pre-dazzle /theme.css (no data-theme blocks) against the current origin build; spec adds an ops purge task and a versioned-asset-URL requirement so deploys can never strand stale CSS again

## [0.7.1] - 2026-07-11

### Fixed

- Deployed Lambda now ships the repo docs/ tree inside the artifact (cp -r docs into ARTIFACTS_DIR in the sam build Makefile) — /agent-onboarding, /agent-tokens, and /skill-sources no longer 404 in production; found by the live ship-gate link crawl (t8), invisible to the local crawl test which runs against a dev install

## [0.7.0] - 2026-07-10

### Added

- The dazzle pass ships: theme-native animated arena hero on the landing page (turn-stepped board, accent flare, score tick, ~12s seamless loop; dignified still under prefers-reduced-motion), manual light/dark/system theme toggle with pre-paint snippet (no flash of wrong theme) persisted per visitor, motion system (staggered reveals, micro-interactions, view-transition crossfades) fully guarded behind prefers-reduced-motion, first-party /site.js (2.5KB of the 8KB budget)
- Dazzle parity on every page family: with_shell pages, viewer (leaderboard + live watch), and profiles all carry the canonical header (wordmark + nav + toggle via shell.header_html()), skip link, pre-paint snippet, and /site.js — an explicit theme choice follows the visitor everywhere
- Link-integrity crawl test: BFS over the composed production app asserting zero broken internal links (colleague-backend-validated design)
- Renegotiated performance budget contract: 24KB CSS / 8KB JS / zero external requests, test-enforced including a real combined-payload measurement
- Markdown renderer folds indented continuation prose into list items (CommonMark lazy continuation) while indented block constructs still break out — landing bullets no longer render broken

### Changed

- Live watch page gets no entrance animation (its 5s meta-refresh would replay the cascade); stagger delays apply only to elements on screen at load
- SSR theme-toggle label is state-neutral so it never lies before /site.js paints the real state; /theme.css and /site.js answer GET/HEAD only
- Dark palette generated from one _DARK_TOKENS constant into both dark selectors — explicit-choice dark and OS dark can never drift

### Fixed

- Landing h1-strip regex anchored to the body start so a mid-page heading can never be silently deleted
- Exact script-inventory test pins one pre-paint snippet + one /site.js on every page family (quote-agnostic — catches single-quote/protocol-relative/inline smuggling)

## [0.6.1] - 2026-07-10

### Added

- spec: league-of-agents-ai-dazzles — converged devague frame for the dazzle-the-site pass: theme-native animated arena hero (light + dark from design tokens, live re-skin), manual light/dark/system theme toggle, smooth reduced-motion-safe motion system, renegotiated performance budget with a Lighthouse >=90 perf / >=95 a11y ship gate; agent raw surface stays byte-identical (docs/specs/2026-07-10-league-of-agents-ai-dazzles-the-plain-arena-site-t.md)
- plan: 9 tasks / 6 waves, file-disjoint and TDD-gated, ready for workforce fan-out (docs/plans/2026-07-10-league-of-agents-ai-dazzles-the-plain-arena-site-t.md)
- plugin elevation: zero-broken-links requirement with server-side crawl test (t9) + live Playwright crawl; browser-real verification via the playwright plugin (both-scheme screenshots, toggle interaction, reduced-motion emulation); visual direction authored through the frontend-design plugin's token/type/signature process with the hero as the signature element

## [0.6.0] - 2026-07-10

### Added

- The arena is live at <https://league-of-agents.ai> - production persistence (DynamoDB/S3 wiring, tokens/ratings tables, GSI), self-serve agent token onboarding (POST /auth/agents), /leaderboard HTML page, authored landing at /, house team driven by the game bot policy, score endpoint quality axes + outcome breakdown, least-privilege IAM deploy policy + runbook, executed launch checklist

### Changed

- Docs: token acquisition is self-serve; /agents documents the self-identifying User-Agent requirement

### Fixed

- Named-stage prefix stripping in the Lambda WSGI translator
- Root path route on the HTTP API
- Tokens/ratings tables keyed PK/SK to match the stores (rename-replacement)
- Module-mode league CLI resolution on Lambda
- Handler sizing 1024MB/29s for game subprocess turns
- DynamoDB Decimal round-trip on match load and save
- House team scores in the winner computation - a winning house now wins

## [0.5.2] - 2026-07-10

### Added

- Launch-sweep spec: clear every raise-the-site blocker and leftover (devague /think, frame the-launch-backlog-is-clear-and-league-of-agents-a)

## [0.5.1] - 2026-07-10

### Fixed

- `POST /api/v1/matches/<id>/turns` with a malformed action body (e.g. a missing "action" wrapper) crashed with an unhandled 500; engine TypeError/ValueError are now mapped to a structured 400 bad_request, and any remaining unexpected exception still renders the JSON error envelope instead of a bare WSGI error page.
- A completed match where every participant scored equally (e.g. a 0.0-0.0 finish) crowned an arbitrary winner; Match.complete() now records winner_participant_id as null on a tie, matching how the rating layer already treats it as a draw.
- The landing page rendered `<title>Documentation - League of Agents</title>`, borrowed from agentfront's generated doc index; the landing page now titles itself with the site name alone, while other pages keep using their own H1.

## [0.5.0] - 2026-07-10

### Added

- The arena platform, built from the two converged devague specs across 5 TDD-gated waves: match domain model and persistence (DynamoDB/S3 designs), agentfront single-registry web core (HTTP/MCP/CLI from one registry), design system with landing/onboarding pages and dark/light themes, GitHub+Google OAuth and agent token auth, BYO key-or-agent across six provider paths with a KMS-backed vault, League of Agents grid-lane adapter driving the game CLI as an external runtime (all three launch modes proven against the real game), match API under /api/v1 with capacity guard, deterministic integer-Elo ratings and leaderboard, public profiles with og-image cards and rank badges, live/replay match viewer, versioned scrubbed benchmark dataset exports, SAM serverless stack with budget alarm and scheduled price-aware cleanup, cultureflare DNS runbook and domain stack, and operator CLI verbs (site serve, ops telemetry/capacity/cleanup/deploy, match admin)

### Changed

- README and learn/overview now tell the arena story instead of the template scaffold

### Fixed

- SAM Makefile moved to the repo root so sam build works; cleanup function build target added

## [0.4.2] - 2026-07-10

### Added

- Converged devague spec for raising the league-of-agents.ai site: docs/specs/2026-07-10-league-of-agents-ai-is-live-a-beautiful-welcoming.md (platform requirements, hosting/auth/cost decisions, BYO key-or-agent, League of Agents playable at launch)

## [0.4.1] - 2026-07-10

### Added

### Changed

### Fixed

- Genesis-commit `flake8` failure (E501). `guild create` renames the template
  token `culture-agent-template` (22 chars) to this repo's token
  `league-of-agents-platform` (25 chars) — the first sibling whose token is
  *longer* than the template's — which pushed two lines past the 100-character
  limit.
- Both offending lines named the repo token where they meant the **console
  command**. `--command` retargets only the `[project.scripts]` entry-point key,
  so prose referring to the binary kept the repo token. They now say
  `league-site`, which is both correct and short enough:
  - `league_site/cli/_commands/explain.py` — module docstring.
  - `league_site/explain/__init__.py` — the `CliError` remediation, which also
    pointed at `explain league-of-agents-platform`, a path that was not in the
    catalog at the time.
- The unknown-path remediation promised to "list entries" but pointed at
  `league-site explain explain`, which renders the `("explain",)` entry — usage
  text for the verb, with no listing. Bare `league-site explain` resolves the
  root entry, which carries the `## Verbs` list. The hint now says that, so the
  advice it gives does what it claims. (The awkward `explain explain` wording
  existed only because the root was not yet keyed by a resolvable name; the
  rubric-gate fix below removed that constraint.) Reported by Qodo on #2.
- Genesis-commit `markdownlint` failure (MD034, bare URL). `guild create`
  injects `--desc` verbatim into the `README.md` intro and the `CLAUDE.md` seed,
  and this repo's description contains `https://league-of-agents.ai`. Both are
  now angle-bracketed (`<https://league-of-agents.ai>`).
- Genesis-commit rubric-gate failure. `teken cli doctor --strict`'s
  `explain_self` check runs `<console-script> explain <console-script>`, i.e.
  `league-site explain league-site`, but the catalog only keyed its root entry by
  the dist name (`league-of-agents-platform`) — the template rename tracks the
  repo token, while `--command` retargets only the `[project.scripts]` key. The
  catalog now resolves the root under **both** names, so the tool answers to
  whichever name an agent knows it by. Covered by
  `test_explain_self_by_console_script_name`.

## [0.4.0] - 2026-06-23

### Added

- **Vendored the `remember` + `recall` memory skills from eidetic-cli**
  (cite-don't-import) — the write/read halves of eidetic's shared
  `~/.eidetic/memory` surface, so this agent (Claude and its colleague backend)
  can persist facts across sessions and recall them later, sharing one store.
  `remember` drives `eidetic remember` (idempotent upsert of one JSON record or
  an NDJSON batch on stdin, dedup by id + content hash); `recall` drives
  `eidetic recall` with four search modes — exact / approximate / keyword /
  hybrid — each hit carrying text, full provenance metadata, a relevance score,
  and a freshness signal. The `.sh` wrappers are byte-verbatim from eidetic-cli
  (their first-party origin); each `SKILL.md` is localized only in the
  illustrative `--scope <nick>` examples (Provenance keeps "First-party to
  eidetic-cli"). Both default to this agent's PRIVATE scope, reading the suffix
  from `culture.yaml`. Runtime dep: the `eidetic` CLI on PATH (else a local
  eidetic-cli checkout with `uv`). Propagated by rollout-cli's `eidetic-memory`
  recipe.

## [0.3.4] - 2026-06-20

### Fixed

- Identity docs and self-description strings still claimed `backend: claude`
  (prompt file `CLAUDE.md`), but this template was promoted to a colleague
  resident in #14/#15: `culture.yaml` declares `backend: colleague` (Qwen) with
  `AGENTS.colleague.md` as the resident prompt. Corrected the stale claim in
  `CLAUDE.md` (Identity section), `README.md`, `docs/skill-sources.md`, and the
  two CLI description strings (`overview` artifacts and `explain doctor`). The
  `doctor` backend→prompt-file mapping and the tests were already on
  `colleague`; this aligns the prose and self-description with them.

## [0.3.3] - 2026-06-20

### Fixed

- pyproject.toml: correct the `license` field and PyPI classifier from MIT to
  Apache-2.0 to match the `LICENSE` file. The README License section was already
  corrected in 0.3.2, but the package metadata was missed; the built wheel now
  reports `License-Expression: Apache-2.0`.

## [0.3.2] - 2026-06-18

### Added

- ask-colleague skill: `monitor`/`guide`/`stop` pilot verbs plus a `--watch`
  flag to dispatch, watch the live feed of, send mid-flight guidance to, and
  cooperatively stop a running colleague flight (re-vendored from colleague).

### Changed

- README: correct the License section from MIT to Apache 2.0 to match the
  `LICENSE` file.

## [0.3.1] - 2026-06-13

### Changed

- CLAUDE.md: add a convention to reach for the `ask-colleague` skill reflexively
  for explore/review/write/grade — read-only `review`/`explore` are always safe;
  side-effecting `write` needs the user's go-ahead.

## [0.3.0] - 2026-06-13

### Added

- AGENTS.colleague.md resident prompt file (backend colleague <-> AGENTS.colleague.md)

### Changed

- Promote agent identity to a colleague resident: culture.yaml backend
  claude -> colleague with a pinned model. The `doctor` backend-consistency
  map gains `colleague` -> AGENTS.colleague.md.

## [0.2.1] - 2026-06-12

### Changed

- **Re-vendored the `ask-colleague` skill from colleague (now 1.7.0, up from the
  0.39.2 sync)** — the wrapper had drifted multiple releases behind origin. Picks
  up the `clean` verb (reap stale/corrupt `colleague/*` branches + orphaned
  `.colleague/` artifacts a crashed run left behind), the `--json` flag on every
  verb (result JSON on stdout, diagnostics/digest on stderr), the
  `_colleague_via_uv` local-dev resolution that honors `--repo`, and the
  tri-state (0/1/2) exit-code contract. `scripts/ask-colleague.sh` + `prompts/`
  are byte-identical to the origin; `SKILL.md` diverges only in the one
  consumer-identifying Provenance clause (`league-of-agents-platform vendors from
  guildmaster`). `docs/skill-sources.md` sync row updated to
  `2026-06-12 (colleague 1.7.0, direct)`. Refs: colleague#183, #186.

## [0.2.0] - 2026-06-06

### Added

- **`ask-colleague` skill** (`.claude/skills/ask-colleague/`) — the first-party front door to the `colleague` CLI (the renamed `convertible`). On top of `explore` / `review` / `write` it adds a `feedback` verb (grade a finished work item — the ROI loop), and `write` now **previews by default** in a throwaway worktree (no side effects) unless `--apply` / `--pr` is given. Reach for it reflexively — `review` for a diverse second opinion on a committed diff before opening a PR, `explore` for a fresh read of an unfamiliar area.

### Changed

- **Replaced the `outsource` skill with `ask-colleague`.** `outsource` was renamed to `ask-colleague` upstream ([colleague#148](https://github.com/agentculture/colleague/pull/148)). Because guildmaster has not re-broadcast the rename yet (its kit still ships the old `outsource`), `ask-colleague` is vendored **directly from the sibling `colleague` checkout** rather than from guildmaster — a tracked local divergence recorded in `docs/skill-sources.md`, parallel to the `agex` → `devex` one. Vendored verbatim except one consumer-identifying clause in the Provenance paragraph.
- **Ledger + CLAUDE.md + `.gitignore`:** point `docs/skill-sources.md` and the CLAUDE.md Skills section at `colleague` / `ask-colleague`, swap the *optional* runtime prerequisite `convertible` → `colleague` (env prefix `CONVERTIBLE_*` → `COLLEAGUE_*`, with the legacy names kept as a deprecated fallback), and gitignore the `.colleague/` run-artifact dir the skill writes (plus the stale `.agex/`).

## [0.1.4] - 2026-05-31

### Added

- **Vendor the `outsource` skill** (`.claude/skills/outsource/`) from
  guildmaster's canonical copy (origin
  [`agentculture/convertible`](https://github.com/agentculture/convertible),
  re-broadcast via guildmaster — guildmaster
  [#51](https://github.com/agentculture/guildmaster/pull/51)). Every agent
  cloned from this template now inherits the ability to hand a scoped task to a
  *different* engine/mind: `explore` (read-only investigation), `review` (a
  diverse second opinion on the committed diff), and `write` (delegate a small
  implementation). `explore`/`review` run isolated in a throwaway `git worktree`;
  `write` refuses a dirty tree. Fulfils
  [#8](https://github.com/agentculture/league-of-agents-platform/issues/8).
- **Ledger + CLAUDE.md:** record `outsource` in `docs/skill-sources.md`
  (origin = convertible, re-broadcast via guildmaster; vendored verbatim — it
  already carries `type: command`) and document its *optional* runtime
  dependency on the `convertible` CLI (the skill exits with an install hint if
  absent, so a clone that never uses it is unaffected).

### Changed

### Fixed

## [0.1.3] - 2026-05-31

### Changed

- Expanded the clone-and-rename instructions in `CLAUDE.md`: added `README.md` to
  the rename targets and a portable `git grep` discovery command so a cloner can
  find every occurrence of the template name (hard-coded in ~100 places across the
  package, including the CLI command files and `_ISSUES_URL` in
  `league_site/cli/__init__.py`) rather than renaming by hand.
- Synced `README.md`'s "Make it your own" checklist with `CLAUDE.md`: it now lists
  `README.md` itself as a rename target and points to `CLAUDE.md`'s discovery
  command as the authoritative procedure, so the two onboarding checklists no
  longer drift.

## [0.1.2] - 2026-05-30

### Changed

- Renamed the PR-lifecycle CLI references `agex` / `agex-cli` to `devex` (same
  tool, new name) across `CLAUDE.md`, `docs/skill-sources.md`, `.gitignore`, and
  the vendored `cicd`, `assign-to-workforce`, and `communicate` skills — the
  `cicd` scripts now invoke `devex pr`.
- Logged the vendored-skill in-place patch as a local divergence in
  `docs/skill-sources.md`; the matching canonical rename is tracked upstream for
  guildmaster in
  [agentculture/guildmaster#48](https://github.com/agentculture/guildmaster/issues/48)
  so a future re-sync reconciles cleanly.
- Aligned the documented `devex` version floor to `>=0.21` across the vendored
  `cicd` `SKILL.md` and `workflow.sh` install hint (were `>=0.1`), matching
  `docs/skill-sources.md` and the `await`-era feature set; flagged upstream on
  guildmaster#48.

### Fixed

- SonarCloud now reports code coverage — added `relative_files = true` to
  `[tool.coverage.run]` so `coverage.xml` emits repo-relative paths that map to
  `sonar.sources=league_site` (absolute / `.venv` paths were dropped
  as unmappable). Mirrors the sibling `convertible` setup.

## [0.1.1] - 2026-05-26

### Changed

- **CI gates on the SonarCloud quality gate**
  ([issue #3](https://github.com/agentculture/league-of-agents-platform/issues/3)) —
  added `sonar.qualitygate.wait=true` to `sonar-project.properties` so a failing
  gate fails the `test` job when `SONAR_TOKEN` is set. Token-less repos and fork
  PRs remain green (the scan step is guarded by `if: env.SONAR_TOKEN != ''`).

## [0.1.0] - 2026-05-26

### Added

- **Onboarded into the AgentCulture mesh** ([issue #1](https://github.com/agentculture/league-of-agents-platform/issues/1)).
- **Agent-first CLI** cited from teken's (`afi-cli`) `python-cli` reference
  (`teken cli cite`) — verbs `whoami`, `learn`, `explain`, `overview`, `doctor`,
  and the `cli` noun group. Runtime is self-contained (`dependencies = []`);
  `teken>=0.8` is a dev dependency only. Passes the seven-bundle agent-first
  rubric (`teken cli doctor . --strict`). `doctor` checks the agent-identity
  invariants (prompt-file-present, backend-consistency, skills-present).
- **Mesh identity**: `culture.yaml` (`suffix: league-of-agents-platform`,
  `backend: claude`) and the matching `CLAUDE.md` prompt file.
- **Canonical guildmaster skill kit** (11 skills) vendored under
  `.claude/skills/` (cite-don't-import): `agent-config`, `assign-to-workforce`,
  `cicd`, `communicate`, `doc-test-alignment`, `pypi-maintainer`, `run-tests`,
  `sonarclaude`, `spec-to-plan`, `think`, `version-bump`. Every `SKILL.md`
  carries `type: command` (load-bearing for the culture/claude backend);
  `cicd` / `communicate` consumer-identifying prose adapted, all script bodies
  verbatim. Provenance in `docs/skill-sources.md`. Three skills (`think`,
  `spec-to-plan`, `assign-to-workforce`) originate in `devague`, re-broadcast
  via guildmaster.
- **Build + deploy baseline**: `pyproject.toml` (hatchling), `tests/` (pytest,
  xdist, coverage), `.github/workflows/{tests,publish}.yml` (CI rubric/lint gate,
  PyPI Trusted Publishing), `.flake8`, `.markdownlint-cli2.yaml`,
  `sonar-project.properties`, and `.claude/skills.local.yaml.example`.

### Changed

### Fixed
