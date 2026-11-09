"""``league-site evals publish|stream`` — upload live-eval data to the evals bucket.

Both verbs are dry-run by default (no S3 client is even built) and act only with
``--apply``. Credentials come from the standard AWS chain, optionally a named profile
(``--profile``, e.g. the match host's ``league-evals``); keys are never accepted as flags
and never printed. Logic lives in :mod:`league_site.evals.publish`.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from league_site.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError
from league_site.cli._output import emit_diagnostic, emit_result
from league_site.evals import publish

_BOTO3_HINT = "install boto3 with `uv sync --extra aws`"
_CRED_HINT = (
    "check the AWS profile/region (the match host uses a named profile in "
    "~/.aws/credentials, see docs/runbooks/evals-match-host.md)"
)

#: Test seam (between ``evals stream`` passes).
_sleep = time.sleep


#: Test seam: ``tests/test_evals_publish.py`` patches this to return a fake S3 client.
def _make_client(profile: str | None = None) -> Any:
    try:
        import boto3
    except ImportError as exc:
        raise CliError(EXIT_ENV_ERROR, "boto3 is required to upload evals", _BOTO3_HINT) from exc
    try:
        return boto3.Session(profile_name=profile).client("s3")
    except Exception as exc:  # noqa: BLE001 - e.g. ProfileNotFound
        raise CliError(
            EXIT_ENV_ERROR, f"cannot build an S3 client: {type(exc).__name__}", _CRED_HINT
        )


def _aws(context: str, fn: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except (CliError, publish.PublishError):
        raise
    except Exception as exc:  # noqa: BLE001 - boto3/botocore failures -> env error
        raise CliError(EXIT_ENV_ERROR, f"{context}: {type(exc).__name__}: {exc}", _CRED_HINT)


def _user_error(exc: publish.PublishError) -> CliError:
    return CliError(
        EXIT_USER_ERROR,
        str(exc),
        "fix the export (re-run the exporter) and publish again; nothing was uploaded",
    )


# --- publish ---------------------------------------------------------------------------


def _plan_table(report: publish.PublishReport) -> str:
    mode = "APPLIED" if report.apply else "dry-run"
    dest = f"s3://{report.bucket}/{report.prefix}"
    lines = [
        f"league-site evals publish ({mode}): {report.eval} -> {dest}",
        f"{'action':<7} {'kind':<14} {'size':>8}  key  [cache-control] reason",
    ]
    for u in report.plan:
        lines.append(
            f"{u.action:<7} {u.kind:<14} {u.size:>8}  {u.key}  "
            f"[{u.headers['Cache-Control']}] {u.reason}"
        )
    if not report.apply:
        lines.append("re-run with --apply to upload (add --compare to check the bucket first)")
    return "\n".join(lines)


def cmd_evals_publish(args: Any) -> int:
    json_mode = bool(getattr(args, "json", False))
    apply = bool(args.apply)
    compare = bool(getattr(args, "compare", False))
    try:
        publish.build_plan(args.from_dir, args.eval, prefix=args.prefix)  # validate first
        client = _make_client(args.profile) if (apply or compare) else None
        report = _aws(
            "uploading the export",
            publish.publish,
            args.from_dir,
            args.eval,
            args.bucket,
            prefix=args.prefix,
            client=client,
            apply=apply,
            compare=compare,
        )
    except publish.PublishError as exc:
        raise _user_error(exc) from exc
    if json_mode:
        emit_result(report.to_dict(), json_mode=True)
    else:
        emit_result(_plan_table(report), json_mode=False)
    return 0


# --- stream ----------------------------------------------------------------------------


def _stream_text(result: dict) -> str:
    mode = "APPLIED" if result["apply"] else "dry-run"
    lines = [f"league-site evals stream ({mode}): {result['eval']}/{result['match']}"]
    lines += [f"  upload {u['key']} ({u['size']} B)" for u in result["uploads"]]
    lines += [f"  delete {d['key']} ({d['reason']})" for d in result["deletes"]]
    lines += [f"  stopped seat {b['seat']} turn {b['turn']}" for b in result["blocked"]]
    return "\n".join(lines)


def cmd_evals_stream(args: Any) -> int:
    json_mode = bool(getattr(args, "json", False))
    apply = bool(args.apply)
    if args.interval <= 0:
        raise CliError(EXIT_USER_ERROR, "--interval must be positive", "e.g. --interval 10")
    live = Path(args.live_dir)
    state_path = live / publish.STATE_FILE
    state = publish.StreamState.load(state_path)
    client = _make_client(args.profile) if apply else None
    try:
        while True:
            try:
                result = _aws(
                    "streaming seat files",
                    publish.stream_pass,
                    live,
                    args.eval,
                    args.match,
                    args.bucket,
                    prefix=args.prefix,
                    client=client,
                    apply=apply,
                    state=state,
                    log=emit_diagnostic,
                )
            except publish.PublishError as exc:
                raise CliError(EXIT_USER_ERROR, str(exc), "check --eval/--match/--prefix") from exc
            if apply and live.is_dir():
                state.save(state_path)
            emit_result(result if json_mode else _stream_text(result), json_mode=json_mode)
            if args.once:
                return 0
            _sleep(args.interval)
    except KeyboardInterrupt:
        return 0


# --- registration ----------------------------------------------------------------------


def _no_verb(args: Any) -> int:
    from league_site.cli._commands.overview import emit_overview

    emit_overview(
        "league-of-agents-platform evals",
        [
            {
                "title": "Verbs",
                "items": [
                    "evals publish --from DIR --eval ID --bucket NAME [--apply] — upload one "
                    "export in reader-safe order (dry-run by default)",
                    "evals stream --eval ID --match TAG --live-dir DIR --bucket NAME [--apply] "
                    "— upload in-progress seat files (dry-run by default)",
                ],
            }
        ],
        json_mode=bool(getattr(args, "json", False)),
    )
    return 0


def _common(p: Any) -> None:
    p.add_argument("--bucket", required=True, help="Evals bucket name (stack output).")
    p.add_argument("--prefix", default=publish.DEFAULT_PREFIX, help="Key prefix (default evals/).")
    p.add_argument(
        "--profile", default=None, help="AWS profile from ~/.aws/credentials (default: chain)."
    )
    p.add_argument("--apply", action="store_true", help="Upload (default: dry-run).")
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")


def register(sub: Any) -> None:
    p = sub.add_parser("evals", help="Publish live-eval data to the evals bucket.")
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=_no_verb, json=False)
    noun_sub = p.add_subparsers(dest="evals_command", parser_class=type(p))

    pub = noun_sub.add_parser(
        "publish", help="Upload one eval export in reader-safe order (dry-run by default)."
    )
    pub.add_argument(
        "--from", dest="from_dir", required=True, help="Export dir (holds index.json)."
    )
    pub.add_argument("--eval", required=True, help="Eval id (a folder in the export dir).")
    pub.add_argument(
        "--compare",
        action="store_true",
        help="In a dry run, read the bucket (HEAD/GET only) to show skip/upload decisions.",
    )
    _common(pub)
    pub.set_defaults(func=cmd_evals_publish)

    st = noun_sub.add_parser(
        "stream", help="Upload in-progress seat files every interval (dry-run by default)."
    )
    st.add_argument("--eval", required=True, help="Eval id.")
    st.add_argument("--match", required=True, help="Match tag.")
    st.add_argument("--live-dir", required=True, help="Dir of <agent_id>.json seat files.")
    st.add_argument("--interval", type=float, default=10.0, help="Seconds between passes.")
    st.add_argument("--once", action="store_true", help="Do a single pass and exit.")
    _common(st)
    st.set_defaults(func=cmd_evals_stream)
