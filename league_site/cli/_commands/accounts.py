"""``league-site accounts`` — operator human-account administration.

The account-level kill-switch (task t4). ``list`` is read-only;
``block``/``unblock`` flip the persisted ``blocked`` flag on a human account
through the store. Blocking an account is broader than blocking one token: it
denies *every* agent token that account has minted (enforced at
:func:`league_site.auth.tokens.verify` time when the account store is wired)
*and* refuses new mints (``POST /auth/agents``) — one store write, read live
on the next request, no deploy and no restart.

Immediate and reversible for the same reason as ``tokens block`` (see that
module): a security kill-switch must take effect now, and ``unblock`` always
undoes it, so there is no dry-run gate. These verbs are net-new CLI-only
capabilities and add no gap to the h9 console↔CLI parity inventory.
"""

from __future__ import annotations

from typing import Any

from league_site.accounts.store import AccountRecord
from league_site.cli._commands import _stores
from league_site.cli._errors import EXIT_USER_ERROR, CliError
from league_site.cli._output import emit_result

#: Shared ``--json`` argparse help string, repeated across the noun and every verb.
_JSON_HELP = "Emit structured JSON."

# --- list --------------------------------------------------------------------


def cmd_accounts_list(args: Any) -> int:
    json_mode = bool(getattr(args, "json", False))
    store, ephemeral = _stores.resolve_account_store()
    records = _stores.guard_aws_call("listing accounts", store.list_all)
    rows = [_row(record) for record in records]
    payload: dict[str, Any] = {"accounts": rows}
    if ephemeral:
        payload["note"] = _stores.TOKENS_EPHEMERAL_NOTE
    if json_mode:
        emit_result(payload, json_mode=True)
    else:
        lines = [f"league-site accounts list: {len(rows)} account(s)"]
        for row in rows:
            lines.append(f"  {row['account_id']}  {row['display_name']}  blocked={row['blocked']}")
        if ephemeral:
            lines.append(f"note: {_stores.TOKENS_EPHEMERAL_NOTE}")
        emit_result("\n".join(lines), json_mode=False)
    return 0


def _row(record: AccountRecord) -> dict[str, Any]:
    return {
        "account_id": record.account_id,
        "provider": record.provider,
        "display_name": record.display_name,
        "blocked": record.blocked,
    }


# --- block / unblock ---------------------------------------------------------


def cmd_accounts_block(args: Any) -> int:
    return _set_blocked(args, True)


def cmd_accounts_unblock(args: Any) -> int:
    return _set_blocked(args, False)


def _set_blocked(args: Any, blocked: bool) -> int:
    json_mode = bool(getattr(args, "json", False))
    account_id = args.account_id
    store, ephemeral = _stores.resolve_account_store()
    # Confirm the account exists up front so a missing id is a clean *user*
    # error (exit 1), not the env error ``guard_aws_call`` would wrap a raw
    # AccountNotFoundError into. ``set_blocked`` then can't raise it.
    existing = _stores.guard_aws_call(f"loading account {account_id!r}", store.get, account_id)
    if existing is None:
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"no account found with id {account_id!r}",
            remediation="list accounts with `league-site accounts list`",
        )
    _stores.guard_aws_call(
        f"{'blocking' if blocked else 'unblocking'} account {account_id!r}",
        store.set_blocked,
        account_id,
        blocked,
    )
    verb = "blocked" if blocked else "unblocked"
    payload: dict[str, Any] = {"account_id": account_id, "blocked": blocked, "status": verb}
    if ephemeral:
        payload["note"] = _stores.TOKENS_EPHEMERAL_NOTE
    if json_mode:
        emit_result(payload, json_mode=True)
    else:
        text = f"{verb} account {account_id}"
        if ephemeral:
            text += f"\nnote: {_stores.TOKENS_EPHEMERAL_NOTE}"
        emit_result(text, json_mode=False)
    return 0


# --- registration ------------------------------------------------------------


def _no_verb(args: Any) -> int:
    from league_site.cli._commands.overview import emit_overview

    emit_overview(
        "league-of-agents-platform accounts",
        [
            {
                "title": "Verbs",
                "items": [
                    "accounts list — read-only summary of every human account (blocked state)",
                    "accounts block <account_id> — block an account and all the tokens it minted",
                    "accounts unblock <account_id> — lift a block",
                ],
            }
        ],
        json_mode=bool(getattr(args, "json", False)),
    )
    return 0


def register(sub: Any) -> None:
    p = sub.add_parser(
        "accounts",
        help="Operator human-account administration (see 'league-site accounts list').",
    )
    p.add_argument("--json", action="store_true", help=_JSON_HELP)
    p.set_defaults(func=_no_verb, json=False)
    noun_sub = p.add_subparsers(dest="accounts_command", parser_class=type(p))

    list_p = noun_sub.add_parser("list", help="Read-only summary of every human account.")
    list_p.add_argument("--json", action="store_true", help=_JSON_HELP)
    list_p.set_defaults(func=cmd_accounts_list)

    block_p = noun_sub.add_parser(
        "block",
        help="Block an account and every agent token it minted (effective next request).",
    )
    block_p.add_argument("account_id", help="The account id, e.g. 'github:12345'.")
    block_p.add_argument("--json", action="store_true", help=_JSON_HELP)
    block_p.set_defaults(func=cmd_accounts_block)

    unblock_p = noun_sub.add_parser("unblock", help="Lift a block on one human account.")
    unblock_p.add_argument("account_id", help="The account id, e.g. 'github:12345'.")
    unblock_p.add_argument("--json", action="store_true", help=_JSON_HELP)
    unblock_p.set_defaults(func=cmd_accounts_unblock)
