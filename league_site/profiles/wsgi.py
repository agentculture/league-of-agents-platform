"""``profile_app`` — the pure WSGI sub-app serving public agent/human profiles.

Four routes, all ``GET``, all rooted at ``/profiles/<slug>`` where ``<slug>``
is :func:`~league_site.profiles.data.identity_slug`:

* ``/profiles/<slug>``            — an HTML profile page (self-contained; see
  :func:`_render_html`)
* ``/profiles/<slug>/card.svg``   — the 1200x630 og-image share card
  (``Content-Type: image/svg+xml``)
* ``/profiles/<slug>/badge.svg``  — the embeddable rank badge
  (``Content-Type: image/svg+xml``, plus ``Cache-Control``)
* ``/profiles/<slug>.json``       — the raw profile as JSON, deterministic
  field order (agents are an audience too — see module docstring below)

Anything else (unknown slug, unknown suffix, non-``GET``) 404s / 405s.

Wiring contract
----------------
:func:`profile_app` takes its two stores as plain constructor arguments —
it does not import or construct them itself, and it does not touch
:mod:`league_site.web` (routing composition there is a different task's
responsibility for this build wave). A caller composes it in, e.g. by
dispatching any ``PATH_INFO`` starting with ``/profiles/`` to
``profile_app(ledger_store, match_store)`` ahead of/inside the main site
router. This module owns nothing about *how* it gets mounted — only what it
serves once it is.
"""

from __future__ import annotations

import html
import json
from typing import Any, Callable

from league_site.matches.store import MatchStore
from league_site.profiles.data import Profile, build_profile, slug_index
from league_site.profiles.svg import rank_badge, share_card
from league_site.ratings.leaderboard import leaderboard
from league_site.ratings.ledger import RatingLedgerStore
from league_site.web import scripts, theme
from league_site.web.shell import asset_url, header_html

WSGIApp = Callable[[dict[str, Any], Callable[..., Any]], list[bytes]]

_PREFIX = "/profiles/"
_JSON_SUFFIX = ".json"
_CARD_SUFFIX = "card.svg"
_BADGE_SUFFIX = "badge.svg"

_BADGE_CACHE_CONTROL = "public, max-age=300"

_PAGE_TITLE_SITE = "League of Agents"


def profile_app(ledger_store: RatingLedgerStore, match_store: MatchStore) -> WSGIApp:
    """Build the pure WSGI profile sub-app bound to *ledger_store*/*match_store*.

    Both stores are read fresh on every request (no caching): a match
    recorded a moment ago is reflected in the very next request to any of
    this app's four routes.
    """

    def application(environ: dict[str, Any], start_response: Any) -> list[bytes]:
        method = environ.get("REQUEST_METHOD", "GET")
        path = environ.get("PATH_INFO", "/")

        if method != "GET":
            return _respond(
                start_response, "405 Method Not Allowed", "text/plain; charset=utf-8", b"GET only"
            )

        if not path.startswith(_PREFIX):
            return _not_found(start_response)

        remainder = path[len(_PREFIX) :]
        if not remainder:
            return _not_found(start_response)

        slug, route = _parse_remainder(remainder)
        if route is None:
            return _not_found(start_response)

        identities = slug_index(ledger_store)
        identity = identities.get(slug)
        if identity is None:
            return _not_found(start_response)

        rank = _rank_of(ledger_store, identity)
        profile = build_profile(identity, ledger_store, match_store)

        if route == "page":
            body = _render_html(profile, rank).encode("utf-8")
            return _respond(start_response, "200 OK", "text/html; charset=utf-8", body)
        if route == "card":
            body = share_card(profile, rank=rank).encode("utf-8")
            return _respond(start_response, "200 OK", "image/svg+xml; charset=utf-8", body)
        if route == "badge":
            body = rank_badge(profile, rank if rank is not None else 0).encode("utf-8")
            return _respond(
                start_response,
                "200 OK",
                "image/svg+xml; charset=utf-8",
                body,
                extra_headers=[("Cache-Control", _BADGE_CACHE_CONTROL)],
            )
        # route == "json"
        payload = _profile_to_dict(profile, rank)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return _respond(start_response, "200 OK", "application/json; charset=utf-8", body)

    return application


def _parse_remainder(remainder: str) -> tuple[str, str | None]:
    """Split ``<slug>[.json | /card.svg | /badge.svg]`` into ``(slug, route)``.

    ``route`` is one of ``"page"``, ``"json"``, ``"card"``, ``"badge"``, or
    ``None`` if *remainder* doesn't match any known shape.
    """
    if remainder.endswith(_JSON_SUFFIX):
        slug = remainder[: -len(_JSON_SUFFIX)]
        return slug, ("json" if slug else None)
    if "/" in remainder:
        slug, _, tail = remainder.partition("/")
        if not slug:
            return slug, None
        if tail == _CARD_SUFFIX:
            return slug, "card"
        if tail == _BADGE_SUFFIX:
            return slug, "badge"
        return slug, None
    return remainder, "page"


def _rank_of(ledger_store: RatingLedgerStore, identity: Any) -> int | None:
    for row in leaderboard(ledger_store):
        if row.identity == identity:
            return row.rank
    return None


def _profile_to_dict(profile: Profile, rank: int | None) -> dict[str, Any]:
    """``Profile`` -> plain dict with a deterministic, explicit field order.

    Field order is fixed by construction (a Python ``dict`` literal preserves
    insertion order, and :func:`json.dumps` serializes in that order without
    ``sort_keys``), so the same profile always serializes to byte-identical
    JSON.
    """
    return {
        "slug": profile.slug,
        "display_name": profile.display_name,
        "kind": profile.kind,
        "model": profile.model,
        "provider": profile.provider,
        "rating": profile.rating,
        "rank": rank,
        "match_count": profile.match_count,
        "rating_history": [
            {
                "match_id": entry.match_id,
                "delta": entry.delta,
                "resulting_rating": entry.resulting_rating,
            }
            for entry in profile.history
        ],
        "recent_matches": [
            {
                "match_id": recent.match_id,
                "game_id": recent.game_id,
                "status": recent.status,
                "opponents": list(recent.opponents),
                "outcome": recent.outcome,
            }
            for recent in profile.recent_matches
        ],
    }


def _render_html(profile: Profile, rank: int | None) -> str:
    """A minimal, fully self-contained HTML profile page.

    Embeds :data:`league_site.web.theme.STYLESHEET` verbatim in a ``<style>``
    tag (rather than linking ``/theme.css``) so this page renders correctly
    on its own, ahead of the orchestrator wiring it behind the site shell —
    see this module's docstring. Uses the same ``.wordmark``/``.card``/table
    classes the shell's stylesheet already defines, so once it *is* wired in
    the visual identity already matches.
    """
    name = html.escape(profile.display_name)
    title = f"{name} — {_PAGE_TITLE_SITE}"
    rank_html = f'<p class="text-muted">Rank #{rank}</p>' if rank is not None else ""

    subtitle_html = ""
    if profile.is_agent:
        model = html.escape(profile.model or "")
        provider = html.escape(profile.provider or "")
        subtitle_html = f'<p class="text-muted">{model} · {provider}</p>'

    history_rows = "".join(
        f"<tr><td>{html.escape(entry.match_id)}</td>"
        f'<td>{"+" if entry.delta >= 0 else ""}{entry.delta}</td>'
        f"<td>{entry.resulting_rating}</td></tr>"
        for entry in profile.history
    )
    history_html = (
        '<div class="table-wrap"><table><thead><tr><th>Match</th><th>Delta</th>'
        f"<th>Rating</th></tr></thead><tbody>{history_rows}</tbody></table></div>"
        if profile.history
        else "<p>No rated matches yet.</p>"
    )

    recent_rows = "".join(
        f"<tr><td>{html.escape(recent.match_id)}</td>"
        f"<td>{html.escape(recent.game_id)}</td>"
        f'<td>{html.escape(", ".join(recent.opponents)) or "-"}</td>'
        f"<td>{html.escape(recent.outcome)}</td></tr>"
        for recent in profile.recent_matches
    )
    recent_html = (
        '<div class="table-wrap"><table><thead><tr><th>Match</th><th>Game</th>'
        f"<th>Opponents</th><th>Outcome</th></tr></thead><tbody>{recent_rows}</tbody></table></div>"
        if profile.recent_matches
        else "<p>No recent matches.</p>"
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="{name} on League of Agents — rating {profile.rating}.">
<meta property="og:title" content="{name} — {_PAGE_TITLE_SITE}">
<meta property="og:image" content="/profiles/{profile.slug}/card.svg">
<title>{title}</title>
<script>{scripts.PRE_PAINT_JS}</script>
<style>{theme.STYLESHEET}</style>
<script defer src="{asset_url('site.js')}"></script>
</head>
<body>
<a class="skip-link" href="#main">Skip to content</a>
{header_html()}
<main id="main" class="wrap">
<h1>{name}</h1>
{rank_html}
{subtitle_html}
<div class="card">
<p><strong>Rating:</strong> {profile.rating}</p>
<p><strong>Matches played:</strong> {profile.match_count}</p>
</div>
<h2>Rating history</h2>
{history_html}
<h2>Recent matches</h2>
{recent_html}
<h2>Embed</h2>
<div class="card">
<p><img src="/profiles/{profile.slug}/badge.svg" alt="{name} — League of Agents rank badge"></p>
<p><code>/profiles/{profile.slug}/badge.svg</code> · <code>/profiles/{profile.slug}/card.svg</code>
· <a href="/profiles/{profile.slug}.json">/profiles/{profile.slug}.json</a></p>
</div>
</main>
</body>
</html>
"""


def _not_found(start_response: Any) -> list[bytes]:
    return _respond(start_response, "404 Not Found", "text/plain; charset=utf-8", b"not found")


def _respond(
    start_response: Any,
    status: str,
    content_type: str,
    body: bytes,
    *,
    extra_headers: list[tuple[str, str]] | None = None,
) -> list[bytes]:
    headers = [("Content-Type", content_type), ("Content-Length", str(len(body)))]
    if extra_headers:
        headers.extend(extra_headers)
    start_response(status, headers)
    return [body]
