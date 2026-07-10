"""``league-of-agents-platform learn`` — the learnability affordance.

Prints a structured self-teaching prompt. Must satisfy the agent-first rubric:
>=200 chars and mention purpose, command map, exit codes, --json, and explain.
"""

from __future__ import annotations

import argparse

from league_site import __version__
from league_site.cli._output import emit_result

_TEXT = """\
league-of-agents-platform — the hosted League of Agents arena at league-of-agents.ai.

Purpose
-------
Hosted platform running the League of Agents arena online: a turn-based game
for humans and agents, for fun and for benchmarks. Continuable matches,
AWS-hosted, with safe-capacity safeguards and price-aware archive/cleanup.
This CLI (console script: league-site) is the local operator surface.

Commands
--------
  league-of-agents-platform whoami             Identity from culture.yaml.
  league-of-agents-platform learn              This self-teaching prompt.
  league-of-agents-platform explain <path>...  Markdown docs for any noun/verb path.
  league-of-agents-platform overview           Descriptive snapshot of the agent.
  league-of-agents-platform doctor             Check the agent-identity invariants.
  league-of-agents-platform cli overview       Describe the CLI surface itself.
  league-of-agents-platform site serve         Serve the site locally (dev).
  league-of-agents-platform ops telemetry      Player/match/provider counters.
  league-of-agents-platform ops capacity       Capacity config + utilization.
  league-of-agents-platform ops cleanup        Archive/delete stale matches (dry-run default).
  league-of-agents-platform ops deploy         Deploy the AWS stack (dry-run default).
  league-of-agents-platform match list|show|archive   Operator match admin.

Machine-readable output
-----------------------
Every command supports --json. Errors in JSON mode emit
{"code", "message", "remediation"} to stderr. Stdout and stderr never mix.

Exit-code policy
----------------
  0 success
  1 user-input error (bad flag, bad path, missing arg)
  2 environment / setup error
  3+ reserved

More detail
-----------
  league-of-agents-platform explain league-of-agents-platform
"""


def _as_json_payload() -> dict[str, object]:
    return {
        "tool": "league-of-agents-platform",
        "version": __version__,
        "purpose": "Hosted platform running the League of Agents arena at league-of-agents.ai.",
        "commands": [
            {"path": ["whoami"], "summary": "Identity probe from culture.yaml."},
            {"path": ["learn"], "summary": "Self-teaching prompt."},
            {"path": ["explain"], "summary": "Markdown docs by path."},
            {"path": ["overview"], "summary": "Descriptive snapshot of the agent."},
            {"path": ["doctor"], "summary": "Check the agent-identity invariants."},
            {"path": ["cli", "overview"], "summary": "Describe the CLI surface."},
            {"path": ["site", "serve"], "summary": "Serve the site locally (dev)."},
            {"path": ["ops", "telemetry"], "summary": "Player/match/provider counters."},
            {"path": ["ops", "capacity"], "summary": "Capacity config + utilization."},
            {"path": ["ops", "cleanup"], "summary": "Archive/delete stale matches (dry-run)."},
            {"path": ["ops", "deploy"], "summary": "Deploy the AWS stack (dry-run default)."},
            {"path": ["match", "list"], "summary": "Operator match admin: list."},
            {"path": ["match", "show"], "summary": "Operator match admin: show one."},
            {
                "path": ["match", "archive"],
                "summary": "Operator match admin: archive one (dry-run default).",
            },
        ],
        "exit_codes": {
            "0": "success",
            "1": "user-input error",
            "2": "environment/setup error",
        },
        "json_support": True,
        "explain_pointer": "league-of-agents-platform explain <path>",
    }


def cmd_learn(args: argparse.Namespace) -> int:
    if getattr(args, "json", False):
        emit_result(_as_json_payload(), json_mode=True)
    else:
        emit_result(_TEXT, json_mode=False)
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "learn",
        help="Print a structured self-teaching prompt for agent consumers.",
    )
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=cmd_learn)
