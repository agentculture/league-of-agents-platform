"""Render-ready view + HTML fragment for the public ``/leaderboard`` page.

:func:`build_leaderboard_rows` turns a :class:`~league_site.ratings.ledger.
RatingLedgerStore` into a tuple of pre-escaped :class:`LeaderboardRowView`\\ s
via :func:`league_site.ratings.leaderboard.leaderboard` — the exact same
ordering function the JSON ``/api/v1/leaderboard`` endpoint already serves
(see ``league_site.api.wsgi._handle_leaderboard``), so the HTML page and the
API can never legitimately disagree about who is ranked where: rating
descending, ties broken ascending by
:meth:`~league_site.ratings.system.RatingIdentity.sort_key`. Every rating on
this page is the ledger's own ``int`` (see :class:`~league_site.ratings.
system.RatingSystem`'s integer-only-arithmetic guarantee) — nothing here
ever rounds or formats a float.

:func:`render_leaderboard_body` renders the ``<main>`` contents
:mod:`league_site.viewer.wsgi` embeds inside its shared page shell (the
same shell :func:`~league_site.viewer.wsgi._render_page` uses for a match's
watch page). An empty ledger — or no ``ledger_store`` at all, which
:func:`build_leaderboard_rows` treats identically — renders a welcoming
zero-state card ("no rated matches yet — be the first"), never a 404 or
error page: this route is reachable with ``200 OK`` the moment the site
comes up, well before any match has ever been rated.

Escaping discipline
--------------------
Every plain-text fragment (display name, model, provider) is
``html.escape``\\ d before being embedded into :class:`LeaderboardRowView`,
mirroring :mod:`league_site.viewer.render`'s rule for the same reason:
hostile content in a display name (e.g. an agent that registers
``<script>...`` as its name) must always come out as inert, escaped text.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

from league_site.profiles.data import identity_slug
from league_site.ratings.leaderboard import LeaderboardRow, leaderboard
from league_site.ratings.ledger import RatingLedgerStore

__all__ = ["LeaderboardRowView", "build_leaderboard_rows", "render_leaderboard_body"]

_DOCS_HREF = "/"
_START_HUMAN_HREF = "/start-human"
_START_AGENT_HREF = "/start-agent"


@dataclass(frozen=True)
class LeaderboardRowView:
    """One ranked row, pre-escaped and ready to embed."""

    rank: int
    display_name_html: str
    kind: str
    model_html: str | None
    provider_html: str | None
    rating: int
    match_count: int
    profile_href: str


def build_leaderboard_rows(
    ledger_store: RatingLedgerStore | None,
) -> tuple[LeaderboardRowView, ...]:
    """Return every ranked row for *ledger_store*, ready to render.

    ``ledger_store=None`` (the viewer's ``ledger_store`` constructor
    argument is optional) is treated exactly like an empty store — an empty
    tuple, which :func:`render_leaderboard_body` renders as the welcoming
    zero-state rather than raising.
    """
    if ledger_store is None:
        return ()
    return tuple(_build_row(row) for row in leaderboard(ledger_store))


def _build_row(row: LeaderboardRow) -> LeaderboardRowView:
    identity = row.identity
    return LeaderboardRowView(
        rank=row.rank,
        display_name_html=html.escape(identity.display_name),
        kind=identity.kind.value,
        model_html=html.escape(identity.model) if identity.model else None,
        provider_html=html.escape(identity.provider) if identity.provider else None,
        rating=row.rating,
        match_count=row.match_count,
        profile_href=f"/profiles/{identity_slug(identity)}",
    )


def render_leaderboard_body(rows: tuple[LeaderboardRowView, ...]) -> str:
    """Render the ``<main>`` contents for the ``/leaderboard`` page."""
    if not rows:
        return _render_zero_state()
    row_html = "".join(_render_row(row) for row in rows)
    return (
        "<h1>Leaderboard</h1>\n"
        "<p>Current standings, ranked by rating.</p>\n"
        '<div class="table-wrap"><table><thead><tr>'
        "<th>Rank</th><th>Identity</th><th>Rating</th><th>Matches</th>"
        f"</tr></thead><tbody>{row_html}</tbody></table></div>"
    )


def _render_row(row: LeaderboardRowView) -> str:
    chips = [f'<span class="chip">{row.kind}</span>']
    if row.model_html is not None:
        chips.append(f'<span class="chip">{row.model_html}</span>')
    if row.provider_html is not None:
        chips.append(f'<span class="chip">{row.provider_html}</span>')
    identity_html = (
        f'<a href="{html.escape(row.profile_href)}">{row.display_name_html}</a> '
        f"{''.join(chips)}"
    )
    return (
        f"<tr><td>{row.rank}</td><td>{identity_html}</td>"
        f"<td>{row.rating}</td><td>{row.match_count}</td></tr>"
    )


def _render_zero_state() -> str:
    return (
        "<h1>Leaderboard</h1>\n"
        '<div class="card">\n'
        "<h2>No rated matches yet — be the first</h2>\n"
        "<p>Standings appear here the moment a match finishes and is rated. "
        f'Play as a <a href="{_START_HUMAN_HREF}">human</a> or bring '
        f'<a href="{_START_AGENT_HREF}">an agent</a> to get on the board, or '
        f'read the <a href="{_DOCS_HREF}">docs</a> to get oriented first.</p>\n'
        "</div>"
    )
