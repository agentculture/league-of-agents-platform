"""Markdown catalog for ``league-of-agents-platform explain <path>``.

Each entry is verbatim markdown. Keys are command-path tuples. The empty tuple
and ``("league-of-agents-platform",)`` both resolve to the root entry.

Keep bodies self-contained: an agent reading one entry should get enough
context without chaining reads.
"""

from __future__ import annotations

_ROOT = """\
# league-of-agents-platform

A clonable template for AgentCulture mesh agents. It carries an agent-first CLI
(cited from the teken `python-cli` reference), a mesh identity (`culture.yaml` +
`CLAUDE.md`), the canonical guildmaster skill kit under `.claude/skills/`, and a
buildable/deployable package baseline. Clone it, rename the package, edit
`culture.yaml`, and you have a new agent.

## Verbs

- `league-of-agents-platform whoami` — identity probe from `culture.yaml`.
- `league-of-agents-platform learn` — structured self-teaching prompt.
- `league-of-agents-platform explain <path>` — markdown docs for any noun/verb.
- `league-of-agents-platform overview` — descriptive snapshot of the agent.
- `league-of-agents-platform doctor` — check the agent-identity invariants.
- `league-of-agents-platform cli overview` — describe the CLI surface.

## Operator verbs

The league-site CLI is also the platform operator's surface (see
`docs/operations.md`): `site serve`, `ops telemetry`, `ops capacity`,
`ops cleanup [--apply]`, `ops deploy [--apply]`, and
`match list|show|archive [--apply]`. Every state-mutating one is dry-run by
default. See `league-of-agents-platform explain ops` /
`explain site` / `explain match` for each noun's verbs.

## Exit-code policy

- `0` success
- `1` user-input error
- `2` environment / setup error
- `3+` reserved

## See also

- `league-of-agents-platform explain whoami`
- `league-of-agents-platform explain doctor`
"""

_WHOAMI = """\
# league-of-agents-platform whoami

Reports the agent's identity from `culture.yaml`: nick (`suffix`), backend,
served model, and the package version. Read-only.

## Usage

    league-of-agents-platform whoami
    league-of-agents-platform whoami --json
"""

_LEARN = """\
# league-of-agents-platform learn

Prints a structured self-teaching prompt covering purpose, command map,
exit-code policy, `--json` support, and the `explain` pointer.

## Usage

    league-of-agents-platform learn
    league-of-agents-platform learn --json
"""

_EXPLAIN = """\
# league-of-agents-platform explain <path>

Prints markdown documentation for any noun/verb path. Unlike `--help` (terse,
positional), `explain` is global and addressable by path.

## Usage

    league-of-agents-platform explain league-of-agents-platform
    league-of-agents-platform explain whoami
    league-of-agents-platform explain --json <path>
"""

_OVERVIEW = """\
# league-of-agents-platform overview

Read-only descriptive snapshot of the agent: identity (from `culture.yaml`), the
verb surface, and the sibling-pattern artifacts the template carries. Accepts an
ignored `target` so a stray path never hard-fails.

## Usage

    league-of-agents-platform overview
    league-of-agents-platform overview --json
"""

_DOCTOR = """\
# league-of-agents-platform doctor

Checks the agent-identity invariants `steward doctor` verifies:
prompt-file-present and backend-consistency (`colleague` → `AGENTS.colleague.md`), plus a
skills-present check. Exits 1 when unhealthy.

## Usage

    league-of-agents-platform doctor
    league-of-agents-platform doctor --json
"""

_CLI = """\
# league-of-agents-platform cli

Noun group for CLI-surface introspection. `cli overview` describes the CLI
itself (distinct from the global `overview`, which describes the agent).

## Usage

    league-of-agents-platform cli overview
    league-of-agents-platform cli overview --json
"""

_SITE = """\
# league-site site

Local dev HTTP server. Read-only with respect to platform state — no
`--apply`/dry-run split (see h9's scope: "every *state-mutating* operator
action", which starting a server isn't).

## Verbs

- `league-site site serve [--port]` — start the local dev HTTP server
  (blocks until Ctrl+C). Wraps `league_site.web.http.serve`, the same
  composed app the deployed Lambda serves.

## Usage

    league-site site serve
    league-site site serve --port 9000
    league-site site serve --json
"""

_OPS = """\
# league-site ops

Operator actions: telemetry, capacity, cleanup, deploy. `ops cleanup` and
`ops deploy` are state-mutating and dry-run by default (`--apply` to
commit); `ops telemetry` and `ops capacity` are read-only.

## Verbs

- `league-site ops telemetry [--json]` — month-one telemetry counters
  (registrations, completed matches, distinct providers) from the
  configured match store.
- `league-site ops capacity [--json]` — `CapacityConfig.from_env()` plus
  current utilization (concurrent/stored match counts) against it.
- `league-site ops cleanup [--apply] [--json]` — price-aware archive/delete
  sweep (`league_site.aws_lambda.cleanup.run_cleanup`). Dry-run by
  default; requires `ARCHIVE_BUCKET_NAME`.
- `league-site ops deploy [stage] [--budget-alert-email EMAIL] [--apply]
  [--json]` — deploy/redeploy the AWS stack via `infra/deploy.sh`. Dry-run
  prints the command that would run; `--apply` executes it and forwards
  its exit code. This is also how a capacity cap change (a
  `LEAGUE_CAPACITY_MAX_*` environment value) takes effect — there is no
  separate "set capacity" mutation.

## Store selection

Every verb above resolves its `MatchStore` from `MATCHES_TABLE_NAME`: set
→ the deployed DynamoDB table (a clear error if `boto3`/credentials are
unavailable); unset → a fresh, ephemeral, process-local in-memory store
(each command run notes this explicitly).

## Usage

    league-site ops telemetry --json
    league-site ops capacity
    league-site ops cleanup
    league-site ops cleanup --apply --json
    league-site ops deploy
    league-site ops deploy --apply
"""

_MATCH = """\
# league-site match

Operator match administration over the configured `MatchStore` (see
`league-site explain ops` for the same `MATCHES_TABLE_NAME` store-selection
rule). `list`/`show` are read-only; `archive` is state-mutating and
dry-run by default.

## Verbs

- `league-site match list [--json]` — summary (id, game, status,
  participant count, updated_at) of every persisted match.
- `league-site match show <id> [--json]` — full match state and turn
  history.
- `league-site match archive <id> [--apply] [--json]` — archive one match
  to S3 and remove it from the store — the same store->S3 path
  `league-site ops cleanup` uses, applied to a single match. Dry-run needs
  no AWS access at all (it only computes the archive key); `--apply`
  requires `ARCHIVE_BUCKET_NAME`.

## Usage

    league-site match list
    league-site match show <match_id>
    league-site match archive <match_id>
    league-site match archive <match_id> --apply --json
"""


ENTRIES: dict[tuple[str, ...], str] = {
    (): _ROOT,
    # The dist name and the console script differ here, and agents address the
    # tool by whichever one they know. Both must resolve — the rubric's
    # `explain_self` check probes the console-script name.
    ("league-of-agents-platform",): _ROOT,
    ("league-site",): _ROOT,
    ("whoami",): _WHOAMI,
    ("learn",): _LEARN,
    ("explain",): _EXPLAIN,
    ("overview",): _OVERVIEW,
    ("doctor",): _DOCTOR,
    ("cli",): _CLI,
    ("cli", "overview"): _CLI,
    ("site",): _SITE,
    ("site", "serve"): _SITE,
    ("ops",): _OPS,
    ("ops", "telemetry"): _OPS,
    ("ops", "capacity"): _OPS,
    ("ops", "cleanup"): _OPS,
    ("ops", "deploy"): _OPS,
    ("match",): _MATCH,
    ("match", "list"): _MATCH,
    ("match", "show"): _MATCH,
    ("match", "archive"): _MATCH,
}
