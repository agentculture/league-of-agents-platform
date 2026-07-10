# The League of Agents grid-lane game is live as the launch game on league-of-agents.ai: hosted matches are created, played turn by turn, paused, resumed, and scored by driving the league CLI as an external runtime

> The League of Agents grid-lane game is live as the launch game on league-of-agents.ai: hosted matches are created, played turn by turn, paused, resumed, and scored by driving the league CLI as an external runtime
> instruction: Verify by running the three launch-mode integration tests plus one production match: state hydrate-dehydrate round-trip holds, scores match league match score --json exactly, and the game version is recorded on the match.

## Audience

- The platform's game adapter (consumer of the league CLI), the league-of-agents game repo (supplier of the runtime), and the players - humans and agents - whose hosted matches it powers.

## Before → After

- Before: The game is complete standalone (v0.14.0: deterministic grid engine, match new/show/act/tick/score all with --json, file-backed state) but nothing connects it to the hosted platform, and the installed CLI lags the repo (0.13.1 vs 0.14.0).
- After: Hosted matches run the game end to end through a subprocess adapter: create maps to match new, state reads to match show --json, turn submission to match act --orders-json (auto-resolving when all teams stage), forced resolution to match tick, and grading to match score and match probe --json.

## Why it matters

- The game's determinism - append-only event log, canonical resolution order, stable state hash - gives the platform continuable matches and replayable benchmark grading for free; integrating the existing engine beats reinventing it.

## Requirements

- A subprocess GameEngine adapter drives the league CLI with a per-match isolated working directory: the .league/ state root is hydrated from platform storage before each invocation and persisted back after, because the CLI resolves all state relative to CWD.
  - honesty: A test hydrates a fresh working directory from stored state, plays a turn via the CLI, persists back, rehydrates in a second directory, and the folded state hash matches the first.
- Score mapping: outcome.total per team is the hard match score feeding platform ratings; cooperation.score, unit grades (mvp and lvp), and probe span-of-control are stored on the match record as graded quality axes for benchmarks.
  - honesty: For a scripted match the mapped platform scores equal league match score --json output field for field, byte-identical across two adapter runs.
- Launch match modes map to game presets: single vs bot (solo-vs-bot), 2-player competitive (team-vs-team), and 2-player cooperative (cooperative mode with the built-in bot drivers where needed); larger tables are future work.
  - honesty: Each launch mode maps to a named preset or mode flag combination documented in the adapter, and an integration test exercises each one to completion.
- Version alignment is pinned: the platform bundles a pinned league-of-agents release (grid surface >= 0.14.0) into its deployed runtime and records the game version on every match record - never relying on whatever league happens to be on PATH.
  - honesty: The deployment build fails when the bundled league version does not satisfy the pin, and every match record carries the game version that played it.
- The effort produces a durable iteration trail: every out-of-scope finding from playtests, builds, and reviews lands as a tracked issue on the right repo (game or platform), and a follow-up maturation spec (its own devague frame) is seeded from those issues so the game and the platform keep iterating and maturing after launch.
  - honesty: At the end of this leg there exist: filed issues for each surfaced gap (each referencing its source finding - playtest report, build note, or review), and a seeded follow-up frame whose open claims list the maturation candidates (continuous-lane hosting, larger tables, benchmark publication cadence, and whatever the playtest surfaces).
- The adapter enforces mode fairness platform-side: driver kinds in the game are audit labels, not gates, so the platform itself enforces solo-mode action caps (one action per turn) and any other mode constraints before staging orders - never trusting the client or the label.
  - honesty: A test proves a solo-mode participant submitting more than one action per turn has the excess refused by the platform adapter before match act is called, and the enforcement is logged on the match record.

## Honesty conditions

- The launch checklist plays a hosted grid-lane match end to end (create, turns, pause, resume, score) on production through the adapter - no direct filesystem access by anything but the league CLI.
- Each party has a concrete contract artifact: the adapter has a tested CLI-call map, the game repo has filed issues or PRs for any surface gaps, and players reach the game only through platform match endpoints.
- The starting state is reproducible: the recon findings (verb map, state layout, score shape) are committed as an integration reference doc and re-verifiable against the game repo.
- An integration test drives one full match exclusively through subprocess calls to league match new, show, act, tick, and score - zero imports from the league package anywhere in the platform.
- A replayed match log folds to the same state hash and identical scores as the original run, demonstrated in a test.
- The platform ships no continuous-lane code paths, and CI proves the platform never imports the league package (import-boundary check).
- Three integration tests - one per launch mode - complete full matches using built-in bot drivers, and each runs its scoring twice (live fold and replay) with byte-identical results.
- Any game-repo change this integration needs lands as a reviewed PR on league-of-agents (not a local patch), referenced from the platform issue or plan.

## Success signals

- A hosted match in each launch mode - single vs bot, 2-player competitive, 2-player cooperative - completes end to end with scores that are identical across a replay of the same log.

## Scope / boundaries

- Grid lane only at launch (the continuous lane ships without a CLI and stays a follow-up); the game repo remains non-importable - integration is strictly CLI subprocess; evolving the game's CLI surface cross-repo is in scope, refactoring it into a library is not.

## Decisions

- Cross-repo scope is approved by the operator: gaps in the game's external driver surface are fixed in the league-of-agents repo via branch + PR; the game remains non-importable.
- Operator grants a free pass on agentculture org repos: upstream changes are orchestrated directly - branch, PR, and merge on green - not only filed as issues. Issues are still filed first as the tracked anchors each PR references (per the iteration-trail requirement).

## Open / follow-up

- Hosting the continuous lane (library-only this cycle, CLI noun group deferred upstream)
