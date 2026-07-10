"""``league-site ops`` — telemetry, capacity, cleanup, and deploy.

The core of h9 ("every state-mutating operator action available anywhere
also exists as a league-site CLI verb with --json output and a dry-run
default"): ``ops cleanup`` and ``ops deploy`` are the two state-mutating
verbs here (both dry-run unless ``--apply``); ``ops telemetry`` and
``ops capacity`` are read-only inspection twins with no dry-run concept.

Capacity's caps (``LEAGUE_CAPACITY_MAX_*``) are themselves only ever changed
by redeploying with different environment values (see
``infra/template.yaml``'s parameters and
:meth:`league_site.capacity.config.CapacityConfig.from_env`) — there is no
separate "set capacity" mutation distinct from a deploy. ``ops deploy`` is
therefore the one CLI verb that covers "capacity change via env" as well as
an ordinary redeploy; ``ops capacity`` only *shows* the config currently in
effect (from ``os.environ`` in this process — the same names the deployed
Lambda reads) plus live utilization against it.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from league_site.capacity.config import CapacityConfig
from league_site.capacity.guard import check_capacity
from league_site.capacity.telemetry import telemetry_snapshot
from league_site.cli._commands import _stores
from league_site.cli._errors import EXIT_ENV_ERROR, CliError
from league_site.cli._output import emit_diagnostic, emit_result
from league_site.matches.match import MatchStatus

#: Test seam — patched by ``tests/test_cli_ops_ops.py`` so ``ops deploy
#: --apply`` never actually shells out during the test suite. Kept as a
#: plain module attribute (not wrapped) so a monkeypatch of
#: ``ops._run_subprocess`` is picked up on every call, mirroring
#: ``_stores.py``'s "individually monkeypatchable seam" pattern.
_run_subprocess = subprocess.run

_TELEMETRY_NOTE = (
    "registrations and distinct_providers read 0 here: no persisted rating-ledger or "
    "agent-token-enumeration adapter is wired up yet (see "
    "league_site.capacity.telemetry's docstring) — only completed_matches reflects "
    "real store state"
)


# --- telemetry --------------------------------------------------------------


def cmd_ops_telemetry(args: Any) -> int:
    json_mode = bool(getattr(args, "json", False))
    store, ephemeral = _stores.resolve_match_store()
    snapshot = _stores.guard_aws_call(
        "reading match telemetry", telemetry_snapshot, match_store=store
    )
    notes = [_TELEMETRY_NOTE]
    if ephemeral:
        notes.insert(0, _stores.EPHEMERAL_NOTE)
    payload: dict[str, Any] = {**snapshot, "notes": notes}
    if json_mode:
        emit_result(payload, json_mode=True)
    else:
        lines = [
            "league-site ops telemetry",
            "",
            f"registrations: {payload['registrations']}",
            f"completed_matches: {payload['completed_matches']}",
            f"distinct_providers: {payload['distinct_providers']}",
        ]
        for note in notes:
            lines.append(f"note: {note}")
        emit_result("\n".join(lines), json_mode=False)
    return 0


# --- capacity ----------------------------------------------------------------


def _current_utilization(store: Any) -> tuple[int, int]:
    """Return ``(concurrent_matches, stored_matches)`` for ``store``.

    Mirrors :func:`league_site.capacity.guard.check_capacity`'s own O(n)
    scan (see that function's docstring for why an O(n) scan is the
    accepted tradeoff) — kept as a small local helper rather than reusing a
    private function of ``guard.py`` across a package boundary.
    """
    match_ids = store.list_ids()
    concurrent = sum(
        1
        for match_id in match_ids
        if store.load(match_id).status in (MatchStatus.ACTIVE, MatchStatus.PAUSED)
    )
    return concurrent, len(match_ids)


def cmd_ops_capacity(args: Any) -> int:
    json_mode = bool(getattr(args, "json", False))
    config = CapacityConfig.from_env()
    store, ephemeral = _stores.resolve_match_store()
    concurrent, stored = _stores.guard_aws_call(
        "reading current match counts", _current_utilization, store
    )
    decision = _stores.guard_aws_call("checking capacity", check_capacity, store, config)
    payload: dict[str, Any] = {
        "config": asdict(config),
        "current": {"concurrent_matches": concurrent, "stored_matches": stored},
        "would_allow_new_match": bool(decision),
        "refusal_reason": None if decision else decision.reason,  # type: ignore[union-attr]
    }
    if ephemeral:
        payload["note"] = _stores.EPHEMERAL_NOTE
    if json_mode:
        emit_result(payload, json_mode=True)
    else:
        lines = [
            "league-site ops capacity",
            "",
            "config:",
        ]
        for key, value in payload["config"].items():
            lines.append(f"  {key}: {value}")
        lines.append("current:")
        lines.append(f"  concurrent_matches: {concurrent}")
        lines.append(f"  stored_matches: {stored}")
        lines.append(f"would_allow_new_match: {payload['would_allow_new_match']}")
        if payload["refusal_reason"]:
            lines.append(f"refusal_reason: {payload['refusal_reason']}")
        if ephemeral:
            lines.append(f"note: {_stores.EPHEMERAL_NOTE}")
        emit_result("\n".join(lines), json_mode=False)
    return 0


# --- cleanup -------------------------------------------------------------


def cmd_ops_cleanup(args: Any) -> int:
    from league_site.aws_lambda.cleanup import run_cleanup

    json_mode = bool(getattr(args, "json", False))
    apply = bool(getattr(args, "apply", False))

    store, ephemeral = _stores.resolve_match_store()
    bucket_name = _stores.resolve_archive_bucket_name()
    archive = _stores.resolve_archive(bucket_name)
    s3_client = _stores.resolve_s3_client()
    config = CapacityConfig.from_env()

    report = _stores.guard_aws_call(
        "running the cleanup sweep",
        run_cleanup,
        match_store=store,
        archive=archive,
        s3_client=s3_client,
        bucket_name=bucket_name,
        config=config,
        dry_run=not apply,
    )
    payload = report.to_dict()
    if ephemeral:
        payload["note"] = _stores.EPHEMERAL_NOTE
    if json_mode:
        emit_result(payload, json_mode=True)
    else:
        mode = "APPLIED" if apply else "dry-run"
        lines = [f"league-site ops cleanup ({mode}): {payload['action_count']} action(s)"]
        for action in payload["actions"]:
            lines.append(f"  [{action['kind']}] {action['match_id']}: {action['reason']}")
        if ephemeral:
            lines.append(f"note: {_stores.EPHEMERAL_NOTE}")
        emit_result("\n".join(lines), json_mode=False)
    return 0


# --- deploy --------------------------------------------------------------


def _find_repo_root() -> Path:
    """Walk up from this file looking for ``infra/deploy.sh``.

    Mirrors :func:`league_site.cli._commands.whoami.find_culture_yaml`'s
    "walk up from ``__file__``" approach: works from an editable/source
    install (what ``uv run`` gives every consumer of this repo); a wheel
    install carries no ``infra/`` directory at all, so this correctly fails
    with a clear remediation rather than silently doing nothing.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "infra" / "deploy.sh").is_file():
            return parent
    raise CliError(
        code=EXIT_ENV_ERROR,
        message="could not locate infra/deploy.sh from this installation",
        remediation="run `league-site ops deploy` from a source checkout of "
        "league-of-agents-platform (a wheel install carries no infra/ directory)",
    )


def _deploy_command(args: Any, repo_root: Path) -> list[str]:
    script = repo_root / "infra" / "deploy.sh"
    command = ["bash", str(script)]
    stage = getattr(args, "stage", None)
    budget_email = getattr(args, "budget_alert_email", None)
    if stage:
        command.append(stage)
    elif budget_email:
        # infra/deploy.sh takes stage-name positionally before
        # budget-alert-email; default the stage to "prod" (deploy.sh's own
        # default) so a bare --budget-alert-email lands in the right slot.
        command.append("prod")
    if budget_email:
        command.append(budget_email)
    return command


def cmd_ops_deploy(args: Any) -> int:
    json_mode = bool(getattr(args, "json", False))
    apply = bool(getattr(args, "apply", False))
    repo_root = _find_repo_root()
    command = _deploy_command(args, repo_root)

    if not apply:
        payload = {"apply": False, "would_run": command}
        if json_mode:
            emit_result(payload, json_mode=True)
        else:
            emit_result(
                "dry-run: would run:\n  " + " ".join(command) + "\nre-run with --apply to execute",
                json_mode=False,
            )
        return 0

    emit_diagnostic(f"running: {' '.join(command)}")
    # In JSON mode, infra/deploy.sh's own progress lines are redirected to
    # our stderr so stdout stays a single clean JSON payload (the
    # stdout-is-results/stderr-is-diagnostics contract from
    # league_site.cli._output); in text mode they pass straight through.
    if json_mode:
        proc = _run_subprocess(command, cwd=str(repo_root), stdout=sys.stderr, stderr=sys.stderr)
    else:
        proc = _run_subprocess(command, cwd=str(repo_root))

    if json_mode:
        emit_result(
            {"apply": True, "command": command, "returncode": proc.returncode}, json_mode=True
        )
    return proc.returncode


# --- registration ----------------------------------------------------------


def _no_verb(args: Any) -> int:
    from league_site.cli._commands.overview import emit_overview

    emit_overview(
        "league-of-agents-platform ops",
        [
            {
                "title": "Verbs",
                "items": [
                    "ops telemetry — read-only month-one telemetry counters",
                    "ops capacity — read-only capacity config + current utilization",
                    "ops cleanup [--apply] — price-aware archive/delete sweep "
                    "(dry-run by default)",
                    "ops deploy [--apply] — deploy/redeploy the AWS stack via "
                    "infra/deploy.sh (dry-run by default)",
                ],
            }
        ],
        json_mode=bool(getattr(args, "json", False)),
    )
    return 0


def register(sub: Any) -> None:
    p = sub.add_parser("ops", help="Operator actions: telemetry, capacity, cleanup, deploy.")
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=_no_verb, json=False)
    noun_sub = p.add_subparsers(dest="ops_command", parser_class=type(p))

    telemetry_p = noun_sub.add_parser("telemetry", help="Read-only month-one telemetry counters.")
    telemetry_p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    telemetry_p.set_defaults(func=cmd_ops_telemetry)

    capacity_p = noun_sub.add_parser(
        "capacity", help="Read-only capacity config + current utilization."
    )
    capacity_p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    capacity_p.set_defaults(func=cmd_ops_capacity)

    cleanup_p = noun_sub.add_parser(
        "cleanup", help="Price-aware archive/delete sweep (dry-run by default)."
    )
    cleanup_p.add_argument(
        "--apply", action="store_true", help="Execute the sweep (default: dry-run)."
    )
    cleanup_p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    cleanup_p.set_defaults(func=cmd_ops_cleanup)

    deploy_p = noun_sub.add_parser(
        "deploy", help="Deploy/redeploy the AWS stack via infra/deploy.sh (dry-run by default)."
    )
    deploy_p.add_argument(
        "stage",
        nargs="?",
        default=None,
        help="Stage name (default: infra/deploy.sh's own default, 'prod').",
    )
    deploy_p.add_argument(
        "--budget-alert-email", default=None, help="Budget alert email (first deploy only)."
    )
    deploy_p.add_argument(
        "--apply", action="store_true", help="Execute the deploy (default: dry-run)."
    )
    deploy_p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    deploy_p.set_defaults(func=cmd_ops_deploy)
