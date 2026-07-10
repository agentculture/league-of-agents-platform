"""``viewer_app`` — the pure WSGI sub-app serving public viewer pages.

Two routes, ``GET`` only, zero auth:

* ``/matches/<id>/watch`` — a self-contained HTML page (inline
  ``<style>{theme.STYLESHEET}</style>``, same convention as
  :mod:`league_site.profiles.wsgi`) showing a match's header (game,
  participants with model/provider chips, status, scores once completed)
  and its full turn-by-turn transcript (:mod:`league_site.viewer.render`).
* ``/leaderboard`` — the public standings page (platform#11): rank,
  identity (linked to ``/profiles/<slug>``, with model/provider chips for
  agents), rating, and matches played, ordered exactly like
  :func:`league_site.ratings.leaderboard.leaderboard` (see
  :mod:`league_site.viewer.leaderboard`). An empty ledger renders a
  welcoming zero-state ("no rated matches yet — be the first"), never a 404
  or error page.

Both share one page shell (:func:`_page_shell`): the canonical site header
(:func:`league_site.web.shell.header_html` — wordmark, primary nav, theme
toggle), the same inline stylesheet, and the same dazzle-layer JS as every
shelled page (the pre-paint theme snippet + ``/site.js``, served site-wide
by ``with_shell``) so a visitor's stored theme choice applies here too.

Live vs. permanent replay (watch page)
----------------------------------------
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
dispatches any ``PATH_INFO`` matching ``/matches/<id>/watch`` or equal to
``/leaderboard`` to ``viewer_app(match_store, ledger_store)`` ahead of the
shell/auth/API stack — see :func:`league_site.web.http.site_app`, which
shares the exact same ``match_store``/``ledger_store`` instances the match
API reads/writes, so a turn recorded a moment ago through ``POST
/api/v1/matches/*/turns`` is visible on the very next ``GET`` of the watch
page, and a match rated a moment ago is visible on the very next ``GET`` of
``/leaderboard``. ``ledger_store`` is optional — omitted, the watch page
simply renders without ratings (see :func:`~league_site.viewer.render.
build_page_model`) and ``/leaderboard`` renders its zero-state.
"""

from __future__ import annotations

import html
import re
from typing import Any, Callable

from league_site.matches.errors import MatchNotFoundError
from league_site.matches.store import MatchStore
from league_site.ratings.ledger import RatingLedgerStore
from league_site.viewer.leaderboard import build_leaderboard_rows, render_leaderboard_body
from league_site.viewer.render import build_page_model, render_page_body
from league_site.web import scripts, theme
from league_site.web.shell import header_html

WSGIApp = Callable[[dict[str, Any], Callable[..., Any]], list[bytes]]

#: The watch-page route: ``GET /matches/<id>/watch``.
WATCH_PATH_RE = re.compile(r"^/matches/(?P<match_id>[^/]+)/watch$")

#: The leaderboard-page route: ``GET /leaderboard`` (platform#11).
_LEADERBOARD_PATH = "/leaderboard"

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

        if path == _LEADERBOARD_PATH:
            if method != "GET":
                return _respond(
                    start_response,
                    "405 Method Not Allowed",
                    "text/plain; charset=utf-8",
                    b"GET only",
                )
            rows = build_leaderboard_rows(ledger_store)
            body = _render_leaderboard_page(rows).encode("utf-8")
            return _respond(start_response, "200 OK", "text/html; charset=utf-8", body)

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
    return _page_shell(
        title=title, description=description, body_html=body_html, refresh_meta=refresh_meta
    )


def _render_leaderboard_page(rows: tuple[Any, ...]) -> str:
    title = f"Leaderboard — {_PAGE_TITLE_SITE}"
    description = "Current standings on League of Agents, ranked by rating."
    body_html = render_leaderboard_body(rows)
    return _page_shell(title=title, description=description, body_html=body_html)


def _page_shell(*, title: str, description: str, body_html: str, refresh_meta: str = "") -> str:
    """The one page shell shared by the watch page and the leaderboard page.

    Renders standalone (ahead of ``with_shell``, per this module's
    docstring) but carries the same dazzle layer as every shelled page: the
    canonical header via :func:`league_site.web.shell.header_html` (wordmark
    + nav + theme toggle), the pre-paint theme snippet, and ``/site.js``
    (served site-wide by ``with_shell``) — so an explicit theme choice
    follows the visitor onto these pages too. The stylesheet stays inline,
    so the page reads perfectly even when no fetch succeeds; ``/site.js``
    is progressive enhancement only (mounted standalone, without the
    composed site serving it, the reference 404s harmlessly: the toggle
    stays inert with its truthful state-neutral label, and theming falls
    back to the pre-paint snippet + OS preference).
    """
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="{description}">
{refresh_meta}<title>{title}</title>
<script>{scripts.PRE_PAINT_JS}</script>
<style>{theme.STYLESHEET}{_EXTRA_STYLE}</style>
<script defer src="/site.js"></script>
</head>
<body>
<a class="skip-link" href="#main">Skip to content</a>
{header_html()}
<main id="main" class="wrap">
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
