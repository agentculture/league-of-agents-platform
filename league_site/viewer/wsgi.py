"""``viewer_app`` — the pure WSGI sub-app serving public viewer pages.

Two routes, ``GET`` only, zero auth:

* ``/matches/<id>/watch`` — a self-contained HTML page (inline
  ``<style>{theme.STYLESHEET}</style>``, same convention as
  :mod:`league_site.profiles.wsgi`) showing a match's header (game,
  participants with model/provider chips, status, scores once completed),
  the live match board when the game publishes one (grid-shaped state —
  :mod:`league_site.viewer.board`; rendered *statically* here: a spectate
  page never carries unit links or move forms, that interaction layer is
  the play surface's alone), and its full turn-by-turn transcript
  (:mod:`league_site.viewer.render`).
* ``/leaderboard`` — the public standings page (platform#11): rank,
  identity (linked to ``/profiles/<slug>``, with model/provider chips for
  agents), rating, and matches played, ordered exactly like
  :func:`league_site.ratings.leaderboard.leaderboard` (see
  :mod:`league_site.viewer.leaderboard`). An empty ledger renders a
  welcoming zero-state ("no rated matches yet — be the first"), never a 404
  or error page.

Both share one page shell (:func:`page_shell`): the canonical site header
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

from league_site.auth import sessions
from league_site.matches.errors import MatchNotFoundError
from league_site.matches.store import MatchStore
from league_site.ratings.ledger import RatingLedgerStore
from league_site.viewer.board import build_board_model, render_board
from league_site.viewer.leaderboard import build_leaderboard_rows, render_leaderboard_body
from league_site.viewer.render import build_page_model, render_page_body
from league_site.web import scripts, theme
from league_site.web.shell import asset_url, header_html

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
/* The match id in the page heading is one long unbreakable token; without
   this it forces page-wide horizontal scroll on phones (which would fight
   the board's own sideways scroll below). */
h1 code { overflow-wrap: anywhere; }

/* --- The match board (league_site.viewer.board) — shared by the spectate
   watch page and the play surface (both render through page_shell). Static
   markup everywhere; only the play surface's overlay adds the interaction
   classes below (unit-selection links, per-cell staging links, staged
   marks), so on a watch page those rules simply never match. No motion:
   every affordance is a static color/border cue, hover included, nothing
   here needs the reduced-motion guard.

   The rival tokens are the red answer to the accent (2026-07-11 feedback:
   "do the red equivalent on the opponent") — defined in all three theme
   blocks exactly like the shared tokens in league_site/web/theme.py. */
:root { --rival: #b3364a; --rival-glow: rgba(179, 54, 74, .18); }
:root[data-theme="dark"] { --rival: #f08a9b; --rival-glow: rgba(240, 138, 155, .24); }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --rival: #f08a9b;
    --rival-glow: rgba(240, 138, 155, .24);
  }
}
/* Cell size: the whole board fits the viewport width — no sideways hunt on
   a phone (2026-07-11 feedback) — capped so it never towers on desktop,
   floored so a pathological grid degrades to the wrap's sideways scroll
   rather than unreadable cells. cqw needs the container; the plain clamp
   is the pre-container-query fallback. */
.board-wrap {
  container-type: inline-size;
  overflow-x: auto;
  max-width: 100%;
  margin: 0 0 var(--space-4);
  padding-bottom: var(--space-2);
}
.board {
  --cell: clamp(2.5rem, 6.5vw, 3.25rem);
  display: grid;
  grid-template-columns: repeat(var(--bw), var(--cell));
  grid-auto-rows: var(--cell);
  width: max-content;
  background-color: var(--surface);
  background-image:
    linear-gradient(to right, var(--border) 1px, transparent 1px),
    linear-gradient(to bottom, var(--border) 1px, transparent 1px);
  background-size: var(--cell) var(--cell);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-sm);
}
@supports (width: 1cqw) {
  .board { --cell: clamp(1.6rem, calc((100cqw - 2px) / var(--bw)), 3.25rem); }
}
.board-post, .board-res, .board-mission, .board-unit {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  min-width: 0;
  min-height: 0;
}
.board-post, .board-res, .board-mission { z-index: 1; }
.board-mark { width: 78%; height: 78%; display: block; }
.board-res .board-mark { fill: var(--mesh-halo); stroke: var(--border-strong); stroke-width: 1; }
/* Mission flags: filled + ink-outlined at near-cell size — they were the
   hardest marks to spot on the live board (2026-07-11 feedback). */
.board-mission .board-mark {
  width: 88%;
  height: 88%;
  fill: var(--mesh-halo-alt);
  stroke: var(--text);
  stroke-width: 2;
}
.board-post-ring { fill: none; stroke: var(--border-strong); stroke-width: 2; }
.board-post[data-owner="none"] .board-post-ring { stroke-dasharray: 4 3; }
.board-post[data-owner="accent"] .board-post-ring { stroke: var(--accent); stroke-width: 3; }
.board-post[data-owner="rival"] .board-post-ring { stroke: var(--rival); stroke-width: 3; }
.board-post-base { fill: var(--surface-2); stroke: var(--border-strong); stroke-width: 1; }
.board-post[data-owner="accent"] .board-post-base { fill: var(--accent); }
.board-post[data-owner="rival"] .board-post-base { fill: var(--rival); }
/* A capture in progress: the streak counter rides the post's corner. */
.board-post-progress {
  position: absolute;
  top: -12%;
  right: -12%;
  font: 700 0.55rem var(--font-mono);
  padding: 0 0.25em;
  border-radius: 999px;
  background: var(--surface);
  border: 1.5px solid var(--accent);
  color: var(--text);
  z-index: 2;
}
.board-post-progress[data-side="rival"] { border-color: var(--rival); }
.board-unit { z-index: 2; text-decoration: none; }
.board-glyph { width: 64%; height: 64%; display: block; position: relative; }
.board-team-accent .board-glyph { fill: var(--accent); }
.board-team-rival .board-glyph { fill: var(--rival); }
/* Carried load: a numbered badge, not an anonymous dot. */
.board-carry {
  position: absolute;
  top: -4%;
  right: -4%;
  min-width: 0.85rem;
  height: 0.85rem;
  border-radius: 999px;
  background: var(--surface);
  border: 1.5px solid var(--border-strong);
  font: 700 0.55rem var(--font-mono);
  color: var(--text);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 0.15em;
}
.board-carry::after { content: attr(data-carry); }
/* Play-only, at rest: a glowing halo + dashed accent ring say "this unit
   is yours to tap" — dashed is this board's whole tappable grammar (the
   reachable-cell targets below speak it too); solid ring = chosen. */
a.board-unit-live::before {
  content: "";
  position: absolute;
  inset: 6%;
  border-radius: 50%;
  background: var(--accent-glow);
  border: 2px dashed var(--accent);
}
a.board-unit-live:hover::before,
a.board-unit-live:focus-visible::before { border-style: solid; }
/* …a solid accent ring marks the unit that is selected… */
.board-unit-selected::before {
  content: "";
  position: absolute;
  inset: 5%;
  border-radius: 50%;
  background: var(--accent-glow);
  border: 2px solid var(--accent);
}
/* …and a quiet solid ring + dimmed glyph mark a unit whose order is
   planned (tap it to change the order). */
a.board-unit-staged { opacity: 0.75; }
a.board-unit-staged::before {
  content: "";
  position: absolute;
  inset: 8%;
  border-radius: 50%;
  border: 2px solid var(--border-strong);
}
/* The planned order, ghosted onto its target cell. */
.board-staged-mark {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  min-width: 0;
  min-height: 0;
  pointer-events: none;
  border: 2px dashed var(--border-strong);
  border-radius: 22%;
  margin: 6%;
}
.board-staged-chip {
  font: 700 0.5rem var(--font-mono);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--text-muted);
  background: var(--surface);
  border-radius: 999px;
  padding: 0 0.3em;
  margin-bottom: -0.4em;
  border: 1px solid var(--border-strong);
}
/* Play-only targets: one staging link per legal order, anchored to its
   cell — plain idempotent GETs (double-tap safe by construction). */
.board-target {
  position: relative;
  z-index: 3;
  display: flex;
  margin: 0;
  min-width: 0;
  min-height: 0;
}
.board-target-link, .board-target-btn {
  flex: 1;
  width: 100%;
  min-width: 0;
  min-height: 0;
  padding: 0;
  cursor: pointer;
  text-decoration: none;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--accent-glow);
  border: 2px dashed var(--accent);
  border-radius: 22%;
}
.board-target-link::after {
  content: "";
  display: block;
  width: 0.5rem;
  height: 0.5rem;
  border-radius: 50%;
  background: var(--accent);
}
.board-target-link:hover, .board-target-link:focus-visible,
.board-target-btn:hover, .board-target-btn:focus-visible {
  background: var(--accent-glow);
  border-style: solid;
}
.board-target-link:hover::after,
.board-target-link:focus-visible::after { background: var(--accent-strong); }
/* Two verbs, one cell: stacked pills, each named — never one ambiguous
   control. */
.board-target-stack {
  flex-direction: column;
  gap: 2px;
  padding: 2px;
  z-index: 4;
}
/* The selected unit's own cell: verb pills anchor to the cell's foot so
   the unit glyph stays visible above them. */
.board-target-self { justify-content: flex-end; }
.board-target-self .board-verb-btn { flex: 0 1 34%; }
.board-verb-btn {
  font: 700 0.55rem var(--font-mono);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--accent-ink);
  background: var(--accent-strong);
  border: 0;
  border-radius: 999px;
}
.board-verb-btn:hover, .board-verb-btn:focus-visible { background: var(--accent); }
/* The play surface's one-line instruction above the board. */
.board-hint {
  font-family: var(--font-mono);
  font-size: var(--text-sm);
  color: var(--text-muted);
  margin: 0 0 var(--space-2);
}
/* --- Play-surface furniture around the board (league_site.play.render). */
.play-notice {
  font-family: var(--font-mono);
  font-size: var(--text-sm);
  color: var(--text);
  background: var(--accent-glow);
  border: 1px solid var(--accent);
  border-radius: var(--radius-sm);
  padding: var(--space-2) var(--space-3);
  margin: 0 0 var(--space-3);
}
.play-status {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin: 0 0 var(--space-2);
}
.play-score {
  font-family: var(--font-mono);
  font-size: var(--text-sm);
  border: 1px solid var(--border);
  border-left: 3px solid var(--accent);
  border-radius: var(--radius-sm);
  padding: 0.1em 0.5em;
  background: var(--surface);
}
.play-score[data-side="rival"] { border-left-color: var(--rival); }
.play-score-turn { border-left-color: var(--border-strong); color: var(--text-muted); }
.play-events { margin: 0 0 var(--space-3); }
.play-events summary, .play-howto summary {
  cursor: pointer;
  font-family: var(--font-mono);
  font-size: var(--text-sm);
  color: var(--text-muted);
}
.play-event-list {
  margin: var(--space-2) 0 0;
  padding-left: 1.25rem;
  font-size: var(--text-sm);
}
.play-plan { list-style: none; padding: 0; margin: 0 0 var(--space-3); }
.play-plan li {
  font-family: var(--font-mono);
  font-size: var(--text-sm);
  padding: 0.15em 0;
}
.play-plan-remove {
  text-decoration: none;
  font-weight: 700;
  padding: 0 0.4em;
  border: 1px solid var(--border);
  border-radius: 999px;
  margin-left: 0.4em;
}
.play-ack { display: flex; align-items: center; gap: var(--space-3); flex-wrap: wrap; }
.play-plan-clear { font-size: var(--text-sm); color: var(--text-muted); }
.play-howto { margin: var(--space-3) 0 0; }
.play-howto ol, .play-howto ul { font-size: var(--text-sm); }
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
        # The shared match board (league_site.viewer.board): spectators see
        # the same board the players play on — statically. No overlay is
        # ever built here, so a watch page never carries unit links or
        # per-cell forms (the play surface's interaction layer is play-only).
        board_model = build_board_model(match.game_state)
        board_html = render_board(board_model) if board_model is not None else None
        body = _render_page(model, board_html).encode("utf-8")
        return _respond(start_response, "200 OK", "text/html; charset=utf-8", body)

    return application


def _render_page(model: Any, board_html: str | None = None) -> str:
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
    body_html = render_page_body(model, board_html=board_html)
    return page_shell(
        title=title, description=description, body_html=body_html, refresh_meta=refresh_meta
    )


def _render_leaderboard_page(rows: tuple[Any, ...]) -> str:
    title = f"Leaderboard — {_PAGE_TITLE_SITE}"
    description = "Current standings on League of Agents, ranked by rating."
    body_html = render_leaderboard_body(rows)
    return page_shell(title=title, description=description, body_html=body_html)


def page_shell(
    *,
    title: str,
    description: str,
    body_html: str,
    refresh_meta: str = "",
    session: sessions.Session | None = None,
) -> str:
    """The one page shell shared by the watch page, the leaderboard page —
    and the play surface (:mod:`league_site.play.wsgi`), which imports it
    rather than forking a fourth copy of the layout.

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

    *session* renders the session-aware account entry in the header (see
    :func:`~league_site.web.shell.header_html`). The viewer's own two pages
    always pass the default ``None`` — they dispatch ahead of ``with_auth``
    and never see a session — while the play surface, mounted inside the
    auth chain, passes the verified session so its pages carry the signed-in
    chip.
    """
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="{description}">
{refresh_meta}<title>{title}</title>
<link rel="icon" type="image/svg+xml" href="{asset_url('favicon.svg')}">
<script>{scripts.PRE_PAINT_JS}</script>
<style>{theme.STYLESHEET}{_EXTRA_STYLE}</style>
<script defer src="{asset_url('site.js')}"></script>
</head>
<body>
<a class="skip-link" href="#main">Skip to content</a>
{header_html(session)}
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
<link rel="icon" type="image/svg+xml" href="{asset_url('favicon.svg')}">
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
