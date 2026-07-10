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
}
