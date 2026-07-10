"""``league-site match`` — operator match administration.

``list``/``show`` are read-only. ``archive`` is the one state-mutating verb
here — the same store->S3 path
:func:`league_site.aws_lambda.cleanup.run_cleanup` uses for its own
hot-stale/overflow passes (write the archive, then delete from the hot
store), applied to a single, operator-chosen match id instead of a whole
sweep. Dry-run by default (h9): the dry-run path never touches S3 or the
match store, so it needs no ``ARCHIVE_BUCKET_NAME``/AWS credentials at
all — only ``--apply`` does.
"""

from __future__ import annotations

from typing import Any

from league_site.cli._commands import _stores
from league_site.cli._errors import EXIT_USER_ERROR, CliError
from league_site.cli._output import emit_result
from league_site.matches.errors import MatchNotFoundError
from league_site.matches.serialization import archive_key, to_archive_dict


def _load_or_raise(store: Any, match_id: str) -> Any:
    try:
        return _stores.guard_aws_call(f"loading match {match_id!r}", store.load, match_id)
    except MatchNotFoundError as exc:
        raise CliError(
            code=EXIT_USER_ERROR,
            message=str(exc),
            remediation="list known ids with `league-site match list`",
        ) from exc


# --- list --------------------------------------------------------------


def cmd_match_list(args: Any) -> int:
    json_mode = bool(getattr(args, "json", False))
    store, ephemeral = _stores.resolve_match_store()
    match_ids = _stores.guard_aws_call("listing matches", store.list_ids)
    summaries = []
    for match_id in match_ids:
        match = _stores.guard_aws_call(f"loading match {match_id!r}", store.load, match_id)
        summaries.append(
            {
                "match_id": match.match_id,
                "game_id": match.game_id,
                "status": match.status.value,
                "participant_count": len(match.participants),
                "updated_at": match.updated_at.isoformat(),
            }
        )
    payload: dict[str, Any] = {"matches": summaries}
    if ephemeral:
        payload["note"] = _stores.EPHEMERAL_NOTE
    if json_mode:
        emit_result(payload, json_mode=True)
    else:
        lines = [f"league-site match list: {len(summaries)} match(es)"]
        for summary in summaries:
            lines.append(
                f"  {summary['match_id']}  {summary['game_id']}  {summary['status']}  "
                f"participants={summary['participant_count']}  updated={summary['updated_at']}"
            )
        if ephemeral:
            lines.append(f"note: {_stores.EPHEMERAL_NOTE}")
        emit_result("\n".join(lines), json_mode=False)
    return 0


# --- show --------------------------------------------------------------


def cmd_match_show(args: Any) -> int:
    json_mode = bool(getattr(args, "json", False))
    store, ephemeral = _stores.resolve_match_store()
    match = _load_or_raise(store, args.match_id)
    payload = to_archive_dict(match)
    if ephemeral:
        payload = {**payload, "note": _stores.EPHEMERAL_NOTE}
    if json_mode:
        emit_result(payload, json_mode=True)
    else:
        lines = [
            f"match_id: {match.match_id}",
            f"game_id: {match.game_id}",
            f"status: {match.status.value}",
            f"participants: {len(match.participants)}",
            f"turns: {len(match.turns)}",
            f"result: {match.result}",
            f"created_at: {match.created_at.isoformat()}",
            f"updated_at: {match.updated_at.isoformat()}",
        ]
        if ephemeral:
            lines.append(f"note: {_stores.EPHEMERAL_NOTE}")
        emit_result("\n".join(lines), json_mode=False)
    return 0


# --- archive -------------------------------------------------------------


def cmd_match_archive(args: Any) -> int:
    json_mode = bool(getattr(args, "json", False))
    apply = bool(getattr(args, "apply", False))
    store, ephemeral = _stores.resolve_match_store()
    match = _load_or_raise(store, args.match_id)
    key = archive_key(match)

    payload: dict[str, Any] = {
        "match_id": match.match_id,
        "archive_key": key,
        "apply": apply,
    }
    if ephemeral:
        payload["note"] = _stores.EPHEMERAL_NOTE

    if not apply:
        payload["status"] = "dry-run"
        if json_mode:
            emit_result(payload, json_mode=True)
        else:
            emit_result(
                f"dry-run: would archive {match.match_id} to {key} and remove it from the "
                "store; re-run with --apply to execute",
                json_mode=False,
            )
        return 0

    bucket_name = _stores.resolve_archive_bucket_name()
    archive = _stores.resolve_archive(bucket_name)
    _stores.guard_aws_call(f"archiving match {match.match_id!r}", archive.archive, match)
    _stores.guard_aws_call(
        f"deleting match {match.match_id!r} from the store", store.delete, match.match_id
    )
    payload["status"] = "archived"
    if json_mode:
        emit_result(payload, json_mode=True)
    else:
        emit_result(
            f"archived {match.match_id} to {key} and removed it from the store", json_mode=False
        )
    return 0


# --- registration ----------------------------------------------------------


def _no_verb(args: Any) -> int:
    from league_site.cli._commands.overview import emit_overview

    emit_overview(
        "league-of-agents-platform match",
        [
            {
                "title": "Verbs",
                "items": [
                    "match list — read-only summary of every persisted match",
                    "match show <id> — full match state and turn history",
                    "match archive <id> [--apply] — archive one match to S3 and remove "
                    "it from the store (dry-run by default)",
                ],
            }
        ],
        json_mode=bool(getattr(args, "json", False)),
    )
    return 0


def register(sub: Any) -> None:
    p = sub.add_parser(
        "match", help="Operator match administration (see 'league-site match list')."
    )
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=_no_verb, json=False)
    noun_sub = p.add_subparsers(dest="match_command", parser_class=type(p))

    list_p = noun_sub.add_parser("list", help="Read-only summary of every persisted match.")
    list_p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    list_p.set_defaults(func=cmd_match_list)

    show_p = noun_sub.add_parser("show", help="Full match state and turn history.")
    show_p.add_argument("match_id", help="The match id to show.")
    show_p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    show_p.set_defaults(func=cmd_match_show)

    archive_p = noun_sub.add_parser(
        "archive",
        help="Archive one match to S3 and remove it from the store (dry-run by default).",
    )
    archive_p.add_argument("match_id", help="The match id to archive.")
    archive_p.add_argument(
        "--apply", action="store_true", help="Execute the archive (default: dry-run)."
    )
    archive_p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    archive_p.set_defaults(func=cmd_match_archive)
