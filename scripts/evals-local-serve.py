#!/usr/bin/env python3
"""Serve the site locally with an evals export and a local-only agent token.

For trying ``/evals/`` and the agent API on one machine, e.g. the pi + Nemotron
tester (``scripts/pi-tester.sh``). It composes the same ``site_app`` the Lambda
serves, with fresh in-memory stores, mounts an export directory (as written by
``python -m league_site.evals.export``) at ``/evals/data/``, and mints one agent
token owned by a local test account. Nothing here touches production: the stores
live in this process and vanish when it stops, so no public rating or
leaderboard can change.

Usage::

    uv run python scripts/evals-local-serve.py --data <export-dir> [--port 8770]

Prints one JSON line to stdout: ``{"url": ..., "token": ..., "account": ...}``.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
from pathlib import Path
from typing import Any
from wsgiref.simple_server import WSGIRequestHandler, make_server

from league_site.accounts.store import AccountRecord, InMemoryAccountStore
from league_site.auth import tokens
from league_site.auth.token_store import InMemoryTokenStore
from league_site.matches import InMemoryMatchStore
from league_site.ratings.ledger import InMemoryRatingLedgerStore

DATA_PREFIX = "/evals/data/"
TEST_ACCOUNT = "github:local-tester"


def _static(root: Path, app: Any) -> Any:
    root = root.resolve()

    def wrapped(environ: dict[str, Any], start_response: Any) -> list[bytes]:
        path = environ.get("PATH_INFO", "")
        if not path.startswith(DATA_PREFIX):
            return app(environ, start_response)
        target = (root / path[len(DATA_PREFIX) :]).resolve()
        if root not in target.parents or not target.is_file():
            start_response("404 Not Found", [("Content-Type", "text/plain; charset=utf-8")])
            return [b"not found"]
        body = target.read_bytes()
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        start_response(
            "200 OK",
            [
                ("Content-Type", f"{ctype}; charset=utf-8"),
                ("Content-Length", str(len(body))),
                ("Cache-Control", "no-cache"),
            ],
        )
        return [body]

    return wrapped


class _Quiet(WSGIRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        sys.stderr.write("%s\n" % (format % args))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", required=True, type=Path, help="evals export directory")
    ap.add_argument("--port", type=int, default=8770)
    args = ap.parse_args(argv)
    if not args.data.is_dir():
        print(f"error: {args.data} is not a directory", file=sys.stderr)
        return 2
    os.environ.setdefault("LEAGUE_EVALS_DATA_BASE", DATA_PREFIX.rstrip("/"))

    from league_site.web.http import site_app  # after the env var is set

    token_store = InMemoryTokenStore()
    account_store = InMemoryAccountStore()
    provider, _, subject = TEST_ACCOUNT.partition(":")
    account_store.upsert(
        AccountRecord(
            account_id=TEST_ACCOUNT,
            provider=provider,
            provider_user_id=subject,
            display_name="Local tester",
            email=None,
        )
    )
    issued = tokens.issue(
        token_store,
        agent_name="pi-nemotron-tester",
        model="nemotron-3.5-super",
        provider="lobes",
        owner_account_id=TEST_ACCOUNT,
    )
    app = site_app(
        match_store=InMemoryMatchStore(),
        token_store=token_store,
        ledger_store=InMemoryRatingLedgerStore(),
        account_store=account_store,
    )
    server = make_server("127.0.0.1", args.port, _static(args.data, app), handler_class=_Quiet)
    url = f"http://127.0.0.1:{server.server_port}"
    print(json.dumps({"url": url, "token": issued.token, "account": TEST_ACCOUNT}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
