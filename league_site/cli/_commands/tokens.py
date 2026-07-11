"""``league-site tokens`` — operator agent-credential administration.

The kill-switch surface for agent bearer tokens (task t4). ``list`` is
read-only; ``block``/``unblock`` flip the persisted ``blocked`` flag through
the store, which the auth path reads on the *very next* request
(:func:`league_site.auth.tokens.verify`) — effective with no deploy and no
restart, unlike a capacity-cap change.

Unlike ``match archive`` (an irreversible store→S3 delete, dry-run by
default under h9), block/unblock are **immediate and fully reversible**: a
security kill-switch that made an operator type ``--apply`` to actually take
effect would be a footgun (an operator who thinks they blocked an abuser but
didn't), and ``unblock`` is always one step from undoing it — so there is no
dry-run gate here. These verbs are net-new CLI-only capabilities (no console
or script path exists that they would need to reach parity with), so they
add no gap to the h9 console↔CLI parity inventory.

Token *values* are never emitted or logged: the store only holds a sha256
hash, and this surface shows the token id (a uuid), the agent name, the
owning account, and the ``revoked``/``blocked`` flags — never the secret or
its hash.
"""

from __future__ import annotations

from typing import Any

from league_site.auth.token_store import TokenRecord
from league_site.cli._commands import _stores
from league_site.cli._errors import EXIT_USER_ERROR, CliError
from league_site.cli._output import emit_result

# --- list --------------------------------------------------------------------


def cmd_tokens_list(args: Any) -> int:
    json_mode = bool(getattr(args, "json", False))
    store, ephemeral = _stores.resolve_token_store()
    records = _stores.guard_aws_call("listing tokens", store.list_all)
    rows = [_row(record) for record in records]
    payload: dict[str, Any] = {"tokens": rows}
    if ephemeral:
        payload["note"] = _stores.TOKENS_EPHEMERAL_NOTE
    if json_mode:
        emit_result(payload, json_mode=True)
    else:
        lines = [f"league-site tokens list: {len(rows)} token(s)"]
        for row in rows:
            lines.append(
                f"  {row['token_id']}  {row['agent_name']}  {row['model']}/{row['provider']}  "
                f"owner={row['owner_account_id']}  revoked={row['revoked']}  "
                f"blocked={row['blocked']}"
            )
        if ephemeral:
            lines.append(f"note: {_stores.TOKENS_EPHEMERAL_NOTE}")
        emit_result("\n".join(lines), json_mode=False)
    return 0


def _row(record: TokenRecord) -> dict[str, Any]:
    return {
        "token_id": record.token_id,
        "agent_name": record.agent_name,
        "model": record.model,
        "provider": record.provider,
        "owner_account_id": record.owner_account_id,
        "revoked": record.revoked,
        "blocked": record.blocked,
    }


# --- block / unblock ---------------------------------------------------------


def cmd_tokens_block(args: Any) -> int:
    return _set_blocked(args, True)


def cmd_tokens_unblock(args: Any) -> int:
    return _set_blocked(args, False)


def _set_blocked(args: Any, blocked: bool) -> int:
    json_mode = bool(getattr(args, "json", False))
    selector = args.selector
    store, ephemeral = _stores.resolve_token_store()
    records = _stores.guard_aws_call("listing tokens", store.list_all)
    record = _resolve_token(records, selector)
    _stores.guard_aws_call(
        f"{'blocking' if blocked else 'unblocking'} token {record.token_id!r}",
        store.set_blocked,
        record.token_id,
        blocked,
    )
    verb = "blocked" if blocked else "unblocked"
    payload: dict[str, Any] = {
        "token_id": record.token_id,
        "agent_name": record.agent_name,
        "blocked": blocked,
        "status": verb,
    }
    if ephemeral:
        payload["note"] = _stores.TOKENS_EPHEMERAL_NOTE
    if json_mode:
        emit_result(payload, json_mode=True)
    else:
        text = f"{verb} token {record.token_id} (agent {record.agent_name!r})"
        if ephemeral:
            text += f"\nnote: {_stores.TOKENS_EPHEMERAL_NOTE}"
        emit_result(text, json_mode=False)
    return 0


def _resolve_token(records: list[TokenRecord], selector: str) -> TokenRecord:
    """Resolve *selector* (a token id or an agent name) to one :class:`TokenRecord`.

    An exact ``token_id`` match wins and is always unambiguous. Otherwise the
    selector is matched by ``agent_name``: the raw operator ``issue`` doesn't
    enforce the name-uniqueness the self-serve mint does (and a revoked token
    frees a name for re-mint), so a name can match more than one record — the
    single *live* (non-revoked) record is preferred, and a genuinely ambiguous
    name is refused in favour of a token id rather than guessing.
    """
    by_id = [record for record in records if record.token_id == selector]
    if by_id:
        return by_id[0]
    by_name = [record for record in records if record.agent_name == selector]
    live = [record for record in by_name if not record.revoked]
    candidates = live if live else by_name
    if not candidates:
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"no token found matching {selector!r}",
            remediation="list tokens (with ids) via `league-site tokens list`",
        )
    if len(candidates) > 1:
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"{selector!r} matches multiple tokens; select by token id instead",
            remediation="list tokens (with ids) via `league-site tokens list`",
        )
    return candidates[0]


# --- registration ------------------------------------------------------------


def _no_verb(args: Any) -> int:
    from league_site.cli._commands.overview import emit_overview

    emit_overview(
        "league-of-agents-platform tokens",
        [
            {
                "title": "Verbs",
                "items": [
                    "tokens list — read-only summary of every agent token (blocked/revoked state)",
                    "tokens block <id-or-name> — block one agent token (effective next request)",
                    "tokens unblock <id-or-name> — lift a block",
                ],
            }
        ],
        json_mode=bool(getattr(args, "json", False)),
    )
    return 0


def register(sub: Any) -> None:
    p = sub.add_parser(
        "tokens", help="Operator agent-credential administration (see 'league-site tokens list')."
    )
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=_no_verb, json=False)
    noun_sub = p.add_subparsers(dest="tokens_command", parser_class=type(p))

    list_p = noun_sub.add_parser("list", help="Read-only summary of every agent token.")
    list_p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    list_p.set_defaults(func=cmd_tokens_list)

    block_p = noun_sub.add_parser(
        "block", help="Block one agent token by token id or agent name (effective next request)."
    )
    block_p.add_argument("selector", help="A token id or an agent name.")
    block_p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    block_p.set_defaults(func=cmd_tokens_block)

    unblock_p = noun_sub.add_parser(
        "unblock", help="Lift a block on one agent token by token id or agent name."
    )
    unblock_p.add_argument("selector", help="A token id or an agent name.")
    unblock_p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    unblock_p.set_defaults(func=cmd_tokens_unblock)
