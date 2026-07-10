# League of Agents game integration reference

How the platform drives the league-of-agents game as an external runtime.
Findings from repo recon and a hands-on playtest of v0.14.0 (2026-07-10);
the authoritative contract is the converged spec in
[docs/specs/2026-07-10-the-league-of-agents-grid-lane-game-is-live-as-the.md](specs/2026-07-10-the-league-of-agents-grid-lane-game-is-live-as-the.md).

## Runtime model

The game is a pure CLI + flat files: no server, no daemon, no importable
library (by owner decision). All state lives under `.league/` **relative to
the working directory** — the platform adapter must run every invocation
from a per-match isolated workdir, hydrated from platform storage before
and persisted back after each call. The append-only event log
(`.league/matches/<id>/log.jsonl`) is the single source of truth and the
native resume point: re-running the CLI against the same directory
continues the match with zero ceremony.

## The grid-lane driver loop

| Platform operation | Game CLI |
|---|---|
| Create match | `league match new --scenario <id> --team <t>... --mode <m> --seed N --apply` |
| Read state | `league match show <id> --json` (`state`, `legal_actions`, `last_turn_rejections`, `staged_teams`) |
| Submit a team's turn | `league match act <id> --team <t> --orders-json <json> --apply` |
| Force resolution | `league match tick <id> --apply` (unstaged teams simply do nothing) |
| Grade | `league match score <id> --json` and `league match probe <id> --json` |

Orders stage per team; the turn auto-resolves once every team has staged.
Score output: `outcome` (per-team integer points: missions + control +
resources — the platform's hard rating score), `cooperation` (0–100 with
signals), `tempo`, and per-unit grades including `mvp`/`lvp`. All are
deterministic folds of the log.

## Adapter rules learned by playing

- **`legal_actions` is load-bearing.** It is an exact precomputed list per
  unit. Never compute legality client-side; always read it fresh, and feed
  `last_turn_rejections` back to whoever chooses the next action.
- **Unit ids are engine-generated** (`<team_id>-u<N>`), not the registered
  agent ids. Read them from `state.units`.
- **Driver kinds are audit labels, not gates.** Mode fairness (e.g. the
  solo mode's one-action-per-turn cap) is enforced only inside the game's
  own harness — the platform adapter must enforce mode constraints itself
  before staging orders.
- **Pin the game version to 0.14.0 or later** and record it on every match
  record. The published 0.13.1 has stale `learn`/`overview` self-teaching
  text that misleads onboarding agents (mechanics are identical).

## Continuous lane (fast-follow, not launch)

The continuous lane (integer-milliunit positions, per-unit decision points,
`outlook` initiative queue, first-class race semantics) has no external
CLI today: matches run only through an in-process library loop that is not
in the published package, and `match show`/`match probe` crash on
continuous logs. Upstream work is tracked on the game repo (new `cmatch`
verbs, unit-scoped act, due-decision reads, packaging). It will need a
structurally different adapter — decision-point-driven and unit-scoped —
not a lane flag on the grid adapter.
