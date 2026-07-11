"""HTML fragments for the play surface: hub bodies + the your-move panel.

The play *board* is not rendered here — the play view reuses the viewer's
board + transcript rendering verbatim (:func:`league_site.viewer.render.
render_page_body`; see that module for the escaping discipline). This module
renders only what the play surface adds around it: the hub's start-a-match
and resume affordances, the per-match "your move" panel (the legal-actions
form, the waiting note, or the final score + replay link), and the error
page body.

Escaping discipline matches the viewer's: every user-derived string —
display names, match/game ids, action labels, action JSON values — goes
through :func:`html.escape` before it is interpolated into markup. Choice
*values* are the canonical JSON of the action
(:attr:`league_site.play.actions.ActionChoice.value`); the round-trip back
is re-validated server-side on submit, never trusted.
"""

from __future__ import annotations

import html
from collections.abc import Sequence

from league_site.matches import Match, MatchStatus
from league_site.play.actions import ActionChoice

#: Fixed copy for the signed-out hub — the "sign in to play" invitation.
_SIGNED_OUT_HTML = """<h1>Play</h1>
<div class="card play-panel">
<p>League of Agents is a turn-based arena for humans and AI agents alike.
Sign in with GitHub to start a match against the house bot and take your
turns right here in the browser — no tooling required.</p>
<p><a class="button" href="/auth/login/github">Sign in with GitHub</a></p>
<p>Just watching? Every match has a public, shareable page — start from the
<a href="/leaderboard">leaderboard</a>. No account needed to spectate.</p>
</div>"""


def render_signed_out_hub_body() -> str:
    """The hub body for an anonymous visitor: an invitation, never a wall."""
    return _SIGNED_OUT_HTML


def render_hub_body(offered_modes: Sequence[str], live_matches: Sequence[Match]) -> str:
    """The signed-in hub body: start-a-match form + the human's live matches."""
    return "\n".join(
        (
            "<h1>Play</h1>",
            _render_start_card(offered_modes),
            _render_live_matches_card(live_matches),
        )
    )


def _render_start_card(offered_modes: Sequence[str]) -> str:
    if not offered_modes:
        return (
            '<div class="card play-panel"><h2>Start a match</h2>'
            "<p>No browser-playable modes are enabled on this deployment.</p></div>"
        )
    options = "".join(
        f'<option value="{html.escape(mode)}">{html.escape(mode)}</option>'
        for mode in offered_modes
    )
    return (
        '<div class="card play-panel">\n'
        "<h2>Start a match</h2>\n"
        "<p>Solo versus the house bot: you move, the bot answers, and the whole "
        "match gets a shareable replay page when it ends.</p>\n"
        '<form method="post" action="/play/matches" class="play-form">\n'
        '<label for="play-mode">Mode</label>\n'
        f'<select id="play-mode" name="mode">{options}</select>\n'
        '<button type="submit" class="button">Start a match</button>\n'
        "</form>\n"
        "</div>"
    )


def _render_live_matches_card(live_matches: Sequence[Match]) -> str:
    if not live_matches:
        items_html = "<p>No live matches yet — start one above.</p>"
    else:
        items = "".join(_render_live_match_item(match) for match in live_matches)
        items_html = f'<ul class="play-match-list">{items}</ul>'
    return f'<div class="card"><h2>Your live matches</h2>{items_html}</div>'


def _render_live_match_item(match: Match) -> str:
    match_id = html.escape(match.match_id)
    return (
        f'<li><a href="/play/matches/{match_id}">Match <code>{match_id}</code></a> '
        f'<span class="chip">{html.escape(match.game_id)}</span>'
        f'<span class="chip">{html.escape(match.status.value)}</span> '
        f"{len(match.turns)} turn(s) so far</li>"
    )


def render_board_lead(
    *,
    selected_unit: str | None,
    selected_role: str | None,
    clear_href: str,
) -> str:
    """The one-line instruction above an interactive board.

    Unselected, it teaches the first tap (pick one of your units); with a
    unit selected it names the unit and offers the way back — a plain GET of
    *clear_href* (the play view without ``?unit=``) clears the selection.
    """
    if selected_unit is None:
        return (
            '<p class="board-hint">Your turn — tap or click one of your glowing units, '
            "then tap where it should go.</p>"
        )
    unit = html.escape(selected_unit)
    role = f" · {html.escape(selected_role)}" if selected_role else ""
    return (
        f'<p class="board-hint"><strong>{unit}</strong>{role} selected — '
        "tap a highlighted square to play it, or "
        f'<a href="{html.escape(clear_href)}">change unit</a>.</p>'
    )


def render_play_panel(
    match: Match,
    *,
    choices: Sequence[ActionChoice],
    show_form: bool,
    waiting: bool,
    watch_href: str,
    collapse_form: bool = False,
) -> str:
    """The panel the play view adds after the reused viewer board rendering.

    Exactly one of the states renders, keyed off the match status the way
    the caller (:mod:`league_site.play.wsgi`) computed it: the legal-actions
    form (*show_form*), the waiting-on-the-other-side note (*waiting*), the
    no-browser-actions degrade, a paused note, or — once the match is over —
    the final score with the shareable replay link.

    *collapse_form* is the board-first mode: when the board itself carries
    the move controls (:mod:`league_site.play.board`), the select-based form
    stays available — accessibility and fallback, never removed — but folds
    into a closed ``<details>`` so the board reads as the primary interface.
    """
    if match.status is MatchStatus.COMPLETED:
        return _render_final_panel(match, watch_href)
    if match.status is MatchStatus.PAUSED:
        return (
            '<div class="card play-panel"><h2>Your move</h2>'
            "<p>This match is paused. Resume it through the API to keep playing.</p></div>"
        )
    if match.status is not MatchStatus.ACTIVE:
        return (
            '<div class="card play-panel"><h2>Your move</h2>'
            "<p>This match has not started yet.</p></div>"
        )
    if show_form:
        return _render_turn_form(match, choices, collapsed=collapse_form)
    if waiting:
        return (
            '<div class="card play-panel"><h2>Your move</h2>'
            "<p>Waiting for the other side — this page refreshes automatically "
            "until it's your turn.</p></div>"
        )
    return (
        '<div class="card play-panel"><h2>Your move</h2>'
        "<p>This match's game doesn't publish browser-playable actions; "
        "take your turn through the API instead.</p></div>"
    )


def _render_turn_form(
    match: Match, choices: Sequence[ActionChoice], *, collapsed: bool = False
) -> str:
    match_id = html.escape(match.match_id)
    options = "".join(
        f'<option value="{html.escape(choice.value)}">{html.escape(choice.label)}</option>'
        for choice in choices
    )
    form = (
        f'<form method="post" action="/play/matches/{match_id}/turns" class="play-form">\n'
        '<label for="play-action">Legal actions</label>\n'
        f'<select id="play-action" name="action">{options}</select>\n'
        '<button type="submit" class="button">Play move</button>\n'
        "</form>"
    )
    if collapsed:
        return (
            '<div class="card play-panel">\n'
            "<h2>Your move</h2>\n"
            "<p>Play straight on the board above — or pick from the list.</p>\n"
            '<details class="play-fallback">\n'
            "<summary>All legal actions as a list</summary>\n"
            f"{form}\n"
            "</details>\n"
            "</div>"
        )
    return f'<div class="card play-panel">\n<h2>Your move</h2>\n{form}\n</div>'


def _render_final_panel(match: Match, watch_href: str) -> str:
    names = {p.participant_id: p.display_name for p in match.participants}
    scores = match.result.scores if match.result is not None else {}
    if scores:
        items = "".join(
            f"<li>{html.escape(names.get(scored_id, scored_id))} "
            f'<span class="chip">score {score:g}</span></li>'
            for scored_id, score in scores.items()
        )
        score_html = f"<ul>{items}</ul>"
    else:
        score_html = "<p>No scores were recorded.</p>"
    return (
        '<div class="card play-panel">\n'
        "<h2>Final score</h2>\n"
        f"{score_html}\n"
        f'<p><a class="button" href="{html.escape(watch_href)}">View the shareable replay</a></p>\n'
        "</div>"
    )


#: The default next step an error page offers when the caller names none.
DEFAULT_ERROR_LINKS: tuple[tuple[str, str], ...] = (("/play", "Back to Play"),)


def render_error_body(
    status: str,
    message: str,
    *,
    links: Sequence[tuple[str, str]] = DEFAULT_ERROR_LINKS,
) -> str:
    """The body of a play-surface error page: honest status, a next step.

    *links* is ``(href, label)`` pairs — e.g. the sign-in URL on a 401, the
    public spectate page on a non-participant 403.
    """
    heading = html.escape(status)
    next_steps = "".join(
        f'<p><a class="button" href="{html.escape(href)}">{html.escape(label)}</a></p>'
        for href, label in links
    )
    return (
        f"<h1>{heading}</h1>\n"
        f'<div class="card play-panel"><p>{html.escape(message)}</p>{next_steps}</div>'
    )


__all__ = [
    "render_signed_out_hub_body",
    "render_hub_body",
    "render_board_lead",
    "render_play_panel",
    "render_error_body",
]
