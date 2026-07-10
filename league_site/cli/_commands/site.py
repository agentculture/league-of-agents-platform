"""``league-site site serve`` — run the local dev HTTP server.

Read-only with respect to platform state: starting a local server mutates
nothing durable (an in-memory match/rating store is created fresh, per
:func:`league_site.web.http.site_app`'s own defaults), so unlike the ``ops``/
``match`` verbs this one carries no ``--apply``/dry-run split — see h9's
scope ("every *state-mutating* operator action"), which ``site serve``
simply isn't.

Wraps :func:`league_site.web.http.serve`, the exact same composed
``site_app()`` the deployed Lambda serves (see that module's docstring), so
``site serve`` and the deployed platform behave identically. That module's
own docstring flagged this verb as "left for a follow-up task/merge to wire
in" — this is that follow-up.
"""

from __future__ import annotations

import argparse

from league_site.cli._output import emit_diagnostic, emit_result
from league_site.web.http import serve as _serve

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8000


def cmd_site_serve(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))
    server = _serve(host=_DEFAULT_HOST, port=args.port)
    payload = {
        "host": _DEFAULT_HOST,
        "port": server.server_port,
        # Loopback-only dev server; plain HTTP is intentional (S5332 exception).
        "url": f"http://127.0.0.1:{server.server_port}",
    }
    if json_mode:
        emit_result(payload, json_mode=True)
    else:
        emit_diagnostic(f"serving {payload['url']} (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _no_verb(args: argparse.Namespace) -> int:
    from league_site.cli._commands.overview import emit_overview

    emit_overview(
        "league-of-agents-platform site",
        [
            {
                "title": "Verbs",
                "items": ["site serve [--port] — start the local dev HTTP server (blocks)."],
            }
        ],
        json_mode=bool(getattr(args, "json", False)),
    )
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("site", help="Local dev HTTP server (see 'league-site site serve').")
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=_no_verb, json=False)
    noun_sub = p.add_subparsers(dest="site_command", parser_class=type(p))

    serve_p = noun_sub.add_parser("serve", help="Start the local dev HTTP server (blocks).")
    serve_p.add_argument(
        "--port",
        type=int,
        default=_DEFAULT_PORT,
        help=f"TCP port to bind (default: {_DEFAULT_PORT}).",
    )
    serve_p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    serve_p.set_defaults(func=cmd_site_serve)
