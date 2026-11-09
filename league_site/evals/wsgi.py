# flake8: noqa: E501
"""``evals_app`` — the ``/evals`` pages: the evals index and the match replay shell.

Pure WSGI, GET/HEAD only, zero auth. Mounted ahead of the shell like the viewer
(see :func:`league_site.web.http.site_app`); it renders its own page inside the
site chrome (:func:`league_site.web.shell.header_html`, pre-paint theme snippet,
``/site.js``).

Routes:

* ``GET /evals/`` — index shell (hero, ledger, benchmarks, players containers).
* ``GET /evals/<eval_id>/`` — 302 to the index anchor ``/evals/#<eval_id>``.
* ``GET /evals/<eval_id>/<match_tag>/`` — replay shell.
* ``GET /evals/replay.js`` / ``/evals/charts.js`` — the page scripts.

Scripts are served as separate, content-hashed, immutable-cached files (the
same ``Cache-Control`` pattern as ``/site.js``) instead of inline, so they stay
outside the shell's 16 KB first-party JS budget; they have their own budget
(:data:`EVALS_JS_BUDGET_BYTES`).

Data is *not* served here: pages fetch ``<data-base>/<eval_id>/manifest.json``
and per-match files, where the base is :data:`DATA_BASE` (override with the
``LEAGUE_EVALS_DATA_BASE`` env var), exposed as ``data-base`` on ``<main>``.
"""

from __future__ import annotations

import hashlib
import html
import os
import re
from typing import Any, Callable

from league_site.evals.charts_js import CHARTS_JS
from league_site.evals.replay_js import REPLAY_JS
from league_site.evals.styles import EVALS_CSS
from league_site.web import fonts, scripts
from league_site.web.shell import asset_url, header_html

WSGIApp = Callable[[dict[str, Any], Callable[..., Any]], list[bytes]]

DATA_BASE = "/evals/data"
DATA_BASE_ENV = "LEAGUE_EVALS_DATA_BASE"

#: Ceiling for the evals scripts together (separate from the shell's JS budget).
EVALS_JS_BUDGET_BYTES = 64 * 1024

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
#: ``data`` is the data prefix, never an eval id.
_RESERVED_IDS = frozenset({"data"})

_INDEX_PATH = "/evals/"
_SCRIPTS: dict[str, bytes] = {
    "replay.js": REPLAY_JS.encode("utf-8"),
    "charts.js": CHARTS_JS.encode("utf-8"),
}
_SCRIPT_HASHES = {n: hashlib.sha256(b).hexdigest()[:8] for n, b in _SCRIPTS.items()}
_TITLE = "Evals — League of Agents"
_HTML = "text/html; charset=utf-8"


def data_base() -> str:
    return (os.environ.get(DATA_BASE_ENV) or DATA_BASE).rstrip("/")


def script_url(name: str) -> str:
    return f"/evals/{name}?v={_SCRIPT_HASHES[name]}"


def _page(body: str, scripts_needed: tuple[str, ...], title: str = _TITLE) -> str:
    tags = "\n".join(f'<script defer src="{script_url(n)}"></script>' for n in scripts_needed)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="Live and replayable agent-vs-agent evaluation matches.">
<title>{html.escape(title)}</title>
<link rel="icon" type="image/svg+xml" href="{asset_url('favicon.svg')}">
<script>{scripts.PRE_PAINT_JS}</script>
<link rel="stylesheet" href="{asset_url('theme.css')}">
<style>{EVALS_CSS}</style>
<script defer src="{asset_url('site.js')}"></script>
{tags}
</head>
<body>
<a class="skip-link" href="#main">Skip to content</a>
{header_html()}
{body}
</body>
</html>
"""


def _index_body() -> str:
    base = html.escape(data_base(), quote=True)
    return f"""<main id="main" class="evals-page" data-page="index" data-base="{base}">
<div class="wrap">
<section class="intro">
<h1>Evals</h1>
<p>Agents play full matches against each other, and every move, order and line of table talk can be replayed turn by turn.</p>
</section>
<p class="muted" id="evals-empty" data-empty>No evals yet. When a run is published, its live match, ledger, benchmarks and player table will appear here.</p>
<noscript><p class="muted">Replays and charts need JavaScript. The raw data is available in the <a href="{base}/manifest.json">data manifest</a>.</p></noscript>
<section id="hero" aria-label="Live match"></section>
<section id="ledger" aria-label="Eval ledger"></section>
<section id="benchmarks" aria-label="Benchmarks"></section>
<section id="players" aria-label="Players"></section>
</div>
</main>"""


def _replay_body(eval_id: str, match_tag: str) -> str:
    base = html.escape(data_base(), quote=True)
    ev = html.escape(eval_id, quote=True)
    mt = html.escape(match_tag, quote=True)
    return f"""<main id="main" class="evals-page" data-page="replay" data-eval="{ev}" data-match="{mt}" data-base="{base}">
<div class="wrap">
<nav class="crumb" aria-label="Breadcrumb"><a href="/evals/#{ev}">&larr; All evals</a></nav>
<noscript><p class="muted">Replays need JavaScript. The raw data is available in the <a href="{base}/{ev}/manifest.json">data manifest</a>.</p></noscript>
<div id="scoreboard" aria-live="polite"></div>
<div class="stage">
<div class="boardcol">
<div class="boardtools">
<div class="seg" role="group" aria-label="Board view">
<button type="button" data-view="full" aria-pressed="true">Full board</button>
<button type="button" data-view="fog" aria-pressed="false">Fog of war</button>
</div>
</div>
<div id="board-host" aria-label="Game board"></div>
<div class="tl-controls">
<button type="button" id="prev" aria-label="Previous turn">&larr; Prev</button>
<button type="button" id="play" aria-label="Play">Play</button>
<button type="button" id="next" aria-label="Next turn">Next &rarr;</button>
<label class="sr" for="turn">Turn</label>
<input type="range" id="turn" min="0" max="0" value="0" aria-label="Turn">
</div>
</div>
<div id="story" aria-label="Match story"></div>
</div>
<div id="moment" aria-live="polite"></div>
</div>
</main>"""


def _respond(
    start_response: Any,
    status: str,
    ctype: str,
    body: bytes,
    extra: tuple[tuple[str, str], ...] = (),
    method: str = "GET",
) -> list[bytes]:
    start_response(status, [("Content-Type", ctype), ("Content-Length", str(len(body))), *extra])
    return [] if method == "HEAD" else [body]


def _redirect(start_response: Any, location: str) -> list[bytes]:
    return _respond(
        start_response, "302 Found", "text/plain; charset=utf-8", b"", (("Location", location),)
    )


def _not_found(start_response: Any, method: str) -> list[bytes]:
    body = _page(
        '<main id="main" class="evals-page"><div class="wrap"><h1>404 — not found</h1>'
        '<p><a href="/evals/">Back to evals</a></p></div></main>',
        (),
        "Not found — League of Agents",
    ).encode("utf-8")
    return _respond(start_response, "404 Not Found", _HTML, body, (), method)


def _valid(ident: str) -> bool:
    return bool(ID_RE.match(ident)) and ident not in _RESERVED_IDS


def evals_app(environ: dict[str, Any], start_response: Any) -> list[bytes]:
    """The ``/evals`` sub-app (see the module docstring for the routes)."""
    method = environ.get("REQUEST_METHOD", "GET")
    if method not in ("GET", "HEAD"):
        return _respond(
            start_response,
            "405 Method Not Allowed",
            "text/plain; charset=utf-8",
            b"method not allowed",
            (("Allow", "GET, HEAD"),),
        )
    path = environ.get("PATH_INFO", "/")

    def page(body: str, needed: tuple[str, ...]) -> list[bytes]:
        encoded = _page(body, needed).encode("utf-8")
        return _respond(start_response, "200 OK", _HTML, encoded, (), method)

    if path == "/evals":
        return _redirect(start_response, _INDEX_PATH)
    if path == _INDEX_PATH:
        return page(_index_body(), ("charts.js", "replay.js"))
    name = path[len(_INDEX_PATH) :]
    if name in _SCRIPTS:
        return _respond(
            start_response,
            "200 OK",
            "text/javascript; charset=utf-8",
            _SCRIPTS[name],
            (("Cache-Control", fonts.CACHE_CONTROL),),
            method,
        )
    parts = name.split("/")
    if len(parts) == 2 and parts[1] == "" and _valid(parts[0]):
        return _redirect(start_response, f"{_INDEX_PATH}#{parts[0]}")
    if len(parts) == 3 and parts[2] == "" and _valid(parts[0]) and _valid(parts[1]):
        return page(_replay_body(parts[0], parts[1]), ("replay.js",))
    return _not_found(start_response, method)
