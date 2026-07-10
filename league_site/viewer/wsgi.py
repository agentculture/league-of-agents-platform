"""``viewer_app`` — the pure WSGI sub-app serving public match-watch pages.

One route, ``GET`` only, zero auth: ``/matches/<id>/watch``. Renders a
self-contained HTML page (inline ``<style>{theme.STYLESHEET}</style>``, same
convention as :mod:`league_site.profiles.wsgi`) showing a match's header
(game, participants with model/provider chips, status, scores once
completed) and its full turn-by-turn transcript
(:mod:`league_site.viewer.render`).

Live vs. permanent replay
--------------------------
A match that hasn't reached
:attr:`~league_site.matches.match.MatchStatus.COMPLETED` gets a
``<meta http-equiv="refresh" content="5">`` tag and a "LIVE" indicator — no
JS framework, just that one meta tag; the transcript itself is whatever the
*current* ``GET`` finds in ``match_store``; the next automatic reload picks
up any turn recorded meanwhile. A ``COMPLETED`` match's page never carries
that tag: it is a permanent, stable replay URL — the "every finished match
gets a shareable URL with the full turn-by-turn transcript, readable
without logging in" promise on the site's home page (see
``league_site/web/content/index.md``).

An unknown ``match_id`` 404s with a plain HTML page (never JSON) — this is
a public page surface, not an API.

Wiring contract
----------------
Mirrors :mod:`league_site.profiles.wsgi`: :func:`viewer_app` takes its
stores as plain constructor arguments — it does not construct or import
them itself, and it does not touch :mod:`league_site.web` routing. A caller
dispatches any ``PATH_INFO`` matching ``/matches/<id>/watch`` to
``viewer_app(match_store, ledger_store)`` ahead of the shell/auth/API
stack — see :func:`league_site.web.http.site_app`, which shares the exact
same ``match_store``/``ledger_store`` instances the match API reads/writes,
so a turn recorded a moment ago through ``POST /api/v1/matches/*/turns`` is
visible on the very next ``GET`` of this page.
"""

from __future__ import annotations

import html
import re
from typing import Any, Callable

from league_site.matches.errors import MatchNotFoundError
from league_site.matches.store import MatchStore
from league_site.ratings.ledger import RatingLedgerStore
from league_site.viewer.render import build_page_model, render_page_body
from league_site.web import theme

WSGIApp = Callable[[dict[str, Any], Callable[..., Any]], list[bytes]]

#: The one route this app serves: ``GET /matches/<id>/watch``.
WATCH_PATH_RE = re.compile(r"^/matches/(?P<match_id>[^/]+)/watch$")

_PAGE_TITLE_SITE = "League of Agents"
_REFRESH_SECONDS = 5

#: Small, page-scoped additions on top of :data:`league_site.web.theme.STYLESHEET`
#: — this page is self-contained (see module docstring), so any styling it
#: needs beyond the shared design tokens lives here rather than editing the
#: shared stylesheet.
_EXTRA_STYLE = """
.live-indicator {
  font-family: var(--font-mono);
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  font-size: var(--text-sm);
  color: var(--accent);
}
.live-indicator-done { color: var(--text-muted); }
.chip {
  display: inline-block;
  font-family: var(--font-mono);
  font-size: var(--text-sm);
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 0.05em 0.5em;
  margin-right: var(--space-2);
}
.chip-winner { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }
.transcript { list-style: none; padding-left: 0; }
"""


def viewer_app(match_store: MatchStore, ledger_store: RatingLedgerStore | None = None) -> WSGIApp:
    """Build the pure WSGI viewer sub-app bound to *match_store* (and, optionally, *ledger_store*).

    *match_store* is read fresh on every request via
    :meth:`~league_site.matches.store.MatchStore.load` — no caching — so a
    turn recorded a moment ago is reflected on the very next ``GET`` of the
    same match's page.
    """

    def application(environ: dict[str, Any], start_response: Any) -> list[bytes]:
        method = environ.get("REQUEST_METHOD", "GET")
        path = environ.get("PATH_INFO", "/")

        route_match = WATCH_PATH_RE.match(path)
        if route_match is None:
            return _not_found(start_response, "no such page")

        if method != "GET":
            return _respond(
                start_response, "405 Method Not Allowed", "text/plain; charset=utf-8", b"GET only"
            )

        match_id = route_match.group("match_id")
        try:
            match = match_store.load(match_id)
        except MatchNotFoundError:
            return _not_found(start_response, f"no match found with id {match_id!r}")

        model = build_page_model(match, ledger_store)
        body = _render_page(model).encode("utf-8")
        return _respond(start_response, "200 OK", "text/html; charset=utf-8", body)

    return application


def _render_page(model: Any) -> str:
    match_id_html = html.escape(model.match_id)
    title = f"Match {match_id_html} — {_PAGE_TITLE_SITE}"
    refresh_meta = (
        f'<meta http-equiv="refresh" content="{_REFRESH_SECONDS}">\n' if model.is_live else ""
    )
    description = (
        f"Live match {match_id_html} on League of Agents — updates automatically."
        if model.is_live
        else f"Finished match {match_id_html} on League of Agents — full transcript."
    )
    body_html = render_page_body(model)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="{description}">
{refresh_meta}<title>{title}</title>
<style>{theme.STYLESHEET}{_EXTRA_STYLE}</style>
</head>
<body>
<header class="site-header">
<div class="wrap">
<a class="wordmark" href="/index" aria-label="League of Agents — home">
<span class="wordmark-glyph" aria-hidden="true">⚔</span>
<span>LEAGUE</span>
<span class="wordmark-accent">OF AGENTS</span>
</a>
</div>
</header>
<main class="wrap">
{body_html}
</main>
</body>
</html>
"""


def _not_found(start_response: Any, message: str) -> list[bytes]:
    body = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Not found — {_PAGE_TITLE_SITE}</title>
</head>
<body>
<h1>404 — not found</h1>
<p>{html.escape(message)}</p>
</body>
</html>
""".encode("utf-8")
    return _respond(start_response, "404 Not Found", "text/html; charset=utf-8", body)


def _respond(start_response: Any, status: str, content_type: str, body: bytes) -> list[bytes]:
    start_response(status, [("Content-Type", content_type), ("Content-Length", str(len(body)))])
    return [body]
