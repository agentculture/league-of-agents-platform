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
from collections.abc import Mapping, Sequence
from typing import Any

from league_site.matches import Match, MatchStatus
from league_site.play.actions import ActionChoice, OrderChoice
from league_site.play.board import play_view_href

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
    staged_count: int = 0,
    plannable_count: int = 0,
) -> str:
    """The one-line instruction above an interactive board.

    Teaches the plan-then-ack loop step by step: pick a unit, pick its
    square, repeat for each unit, then End turn. With a unit selected it
    names the unit and offers the way back — a plain GET of *clear_href*
    (the play view without ``?unit=``) clears the selection.
    """
    if selected_unit is not None:
        unit = html.escape(selected_unit)
        role = f" · {html.escape(selected_role)}" if selected_role else ""
        return (
            f'<p class="board-hint"><strong>{unit}</strong>{role} selected — '
            "tap a highlighted square to plan its move (labeled pills = other "
            f'actions), or <a href="{html.escape(clear_href)}">pick a different unit</a>.</p>'
        )
    if staged_count and staged_count >= plannable_count:
        return (
            '<p class="board-hint">Every unit has its order — press '
            "<strong>End turn</strong> below to play them all.</p>"
        )
    if staged_count:
        return (
            f'<p class="board-hint">{staged_count} of {plannable_count} units planned — '
            "tap the next glowing unit (or press <strong>End turn</strong> below; "
            "unplanned units hold).</p>"
        )
    return (
        '<p class="board-hint">Your turn — plan a move for each unit, then play them '
        "all at once: tap a glowing unit, tap where it should go, repeat, and press "
        "<strong>End turn</strong>.</p>"
    )


#: ``?notice=<kind>`` → the honest one-liner the play view shows after a
#: redirect. Unknown kinds render nothing (a hand-edited URL is not an
#: error page).
_NOTICES: dict[str, str] = {
    "stale": (
        "That turn had already been played (a double-tap, most likely) — "
        "nothing was submitted twice. Here's the fresh board."
    ),
    "illegal": (
        "Part of your plan was no longer legal — the board had moved on. "
        "Nothing was played; plan again from the fresh board."
    ),
    "empty": "Plan at least one order before ending the turn.",
}


def render_notice(kind: str | None) -> str:
    """The post-redirect notice banner for ``?notice=<kind>``, or ``""``."""
    message = _NOTICES.get(kind or "")
    if message is None:
        return ""
    return f'<p class="play-notice" role="status">{html.escape(message)}</p>'


def render_status_strip(
    rows: Sequence[tuple[str, int, int, bool]], *, turn: int | None, turn_limit: int | None
) -> str:
    """The one-line score strip above the board.

    *rows* is ``(team_label, posts_held, banked_resources, is_you)`` per
    team, the viewer's own team first (see the wsgi caller); the turn
    counter rides on the right.
    """
    if not rows:
        return ""
    chips = []
    for label, posts, banked, is_you in rows:
        side = "you" if is_you else "rival"
        chips.append(
            f'<span class="play-score" data-side="{side}"><strong>{html.escape(label)}</strong> '
            f"{posts} post{'s' if posts != 1 else ''} · {banked} banked</span>"
        )
    if turn is not None and turn_limit is not None:
        chips.append(f'<span class="play-score play-score-turn">Turn {turn}/{turn_limit}</span>')
    return f'<p class="play-status">{"".join(chips)}</p>'


def describe_events(
    events: Sequence[Any],
    rejections: Sequence[Any],
    team_labels: Mapping[str, str],
) -> tuple[str, ...]:
    """Plain sentences for the "Last turn" feed.

    *events* is the adapter's ``last_turn_events``
    (:mod:`league_site.game.events`), *rejections* the game's + platform's
    refusal dicts, *team_labels* the ``team id -> "You"/display name``
    mapping the wsgi caller derives. Malformed entries and unknown kinds
    are skipped — this feed narrates, it never crashes a page.
    """
    sentences: list[str] = []
    for event in events:
        if not isinstance(event, Mapping):
            continue
        sentence = _describe_event(event, team_labels)
        if sentence:
            sentences.append(sentence)
    for rejection in rejections:
        if not isinstance(rejection, Mapping):
            continue
        reason = rejection.get("reason")
        if not isinstance(reason, str) or not reason:
            continue
        unit = rejection.get("unit_id")
        subject = str(unit) if isinstance(unit, str) and unit else "an order"
        sentences.append(f"Refused — {subject}: {reason}")
    return tuple(sentences)


def _describe_event(event: Mapping[str, Any], team_labels: Mapping[str, str]) -> str | None:
    kind = event.get("kind")
    team = _team_label(event.get("team"), team_labels)
    if kind == "post_captured":
        return f"{team} captured {event.get('post_id', 'a post')}."
    if kind == "post_lost":
        return f"{team} lost {event.get('post_id', 'a post')}."
    if kind == "gathered":
        amount = event.get("amount", 1)
        return f"{event.get('unit_id', 'a unit')} ({team}) gathered {amount}."
    if kind == "delivered":
        amount = event.get("amount", 1)
        return f"{team} delivered {amount} — it's banked."
    if kind == "node_exhausted":
        return f"Resource node {event.get('node_id', '?')} is empty."
    if kind == "unit_fell":
        return f"{event.get('unit_id', 'a unit')} ({team}) fell."
    if kind == "mission_completed":
        reward = event.get("reward")
        bonus = f" (+{reward})" if isinstance(reward, int) else ""
        return f"{team} completed mission {event.get('mission_id', '?')}{bonus}."
    return None


def _team_label(team: Any, team_labels: Mapping[str, str]) -> str:
    if isinstance(team, str) and team:
        return team_labels.get(team, team)
    return "someone"


def render_events(sentences: Sequence[str]) -> str:
    """The "Last turn" feed card, or ``""`` when nothing happened."""
    if not sentences:
        return ""
    items = "".join(f"<li>{html.escape(sentence)}</li>" for sentence in sentences)
    return (
        '<details class="play-events" open>\n'
        "<summary>Last turn</summary>\n"
        f'<ul class="play-event-list">{items}</ul>\n'
        "</details>"
    )


def render_plan_panel(
    match: Match,
    *,
    orders: Sequence[OrderChoice],
    staged: Sequence[OrderChoice],
    turn: Any,
    play_path: str,
) -> str:
    """The plan-then-ack panel under an interactive board.

    Lists the staged plan (each order removable), carries the one commit
    control — the End-turn POST whose hidden fields are the plan plus the
    ``turn`` index the plan was made against (the stale-double-submit
    guard) — and keeps the select-based staging form available as the
    accessible fallback (``<details>``, like the old list form).
    """
    match_id = html.escape(match.match_id)
    staged_units = {choice.unit_id for choice in staged}
    plannable = sorted({choice.unit_id for choice in orders} | staged_units)
    parts = ['<div class="card play-panel">', "<h2>Your move</h2>"]

    if staged:
        chips = []
        for choice in staged:
            remove_href = play_view_href(
                play_path, [other for other in staged if other is not choice]
            )
            chips.append(
                f'<li>{html.escape(choice.label)} <a class="play-plan-remove" '
                f'href="{html.escape(remove_href)}" aria-label="Remove: '
                f'{html.escape(choice.label)}">✕</a></li>'
            )
        parts.append(f'<ul class="play-plan">{"".join(chips)}</ul>')
    else:
        parts.append(
            "<p>No orders planned yet — tap a glowing unit on the board to start your plan.</p>"
        )

    if staged:
        held = len(plannable) - len(staged_units)
        plural = "s" if held != 1 else ""
        note = f" ({held} unit{plural} will hold)" if held > 0 else ""
        hidden = "".join(
            f'<input type="hidden" name="order" value="{html.escape(choice.value)}">'
            for choice in staged
        )
        parts.append(
            f'<form method="post" action="/play/matches/{match_id}/turns" class="play-ack">\n'
            f"{hidden}"
            f'<input type="hidden" name="turn" value="{html.escape(str(turn))}">\n'
            f'<button type="submit" class="button">End turn — play '
            f"{len(staged)} order{'s' if len(staged) != 1 else ''}{note}</button>\n"
            f'<a class="play-plan-clear" href="{html.escape(play_view_href(play_path, ()))}">'
            "Clear plan</a>\n"
            "</form>"
        )

    unstaged_orders = [choice for choice in orders if choice.unit_id not in staged_units]
    if unstaged_orders:
        options = "".join(
            f'<option value="{html.escape(choice.value)}">{html.escape(choice.label)}</option>'
            for choice in unstaged_orders
        )
        hidden_staged = "".join(
            f'<input type="hidden" name="staged" value="{html.escape(choice.value)}">'
            for choice in staged
        )
        parts.append(
            '<details class="play-fallback">\n'
            "<summary>All legal orders as a list</summary>\n"
            f'<form method="get" action="{html.escape(play_path)}" class="play-form">\n'
            f"{hidden_staged}"
            '<label for="play-stage">Legal orders</label>\n'
            f'<select id="play-stage" name="staged">{options}</select>\n'
            '<button type="submit" class="button">Add to plan</button>\n'
            "</form>\n"
            "</details>"
        )
    parts.append("</div>")
    return "\n".join(parts)


def render_how_to(rules: Mapping[str, Any] | None, *, open_by_default: bool) -> str:
    """The collapsible "How to play" explainer under the panel.

    *rules* is the state's ``scenario_rules`` projection (may be ``None``
    or empty on matches persisted before it existed — the copy then simply
    omits the numbers it doesn't know).
    """
    rules = rules if isinstance(rules, Mapping) else {}
    hold_turns = rules.get("capture_hold_turns")
    hold_note = (
        f"stand on it for {hold_turns} turns in a row"
        if isinstance(hold_turns, int)
        else "stand on it for a few turns in a row"
    )
    roles = rules.get("roles")
    no_capture = (
        sorted(
            role
            for role, stats in roles.items()
            if isinstance(stats, Mapping) and stats.get("can_capture") is False
        )
        if isinstance(roles, Mapping)
        else []
    )
    capture_who = f" ({', '.join(no_capture)} can't capture)" if no_capture else ""
    open_attr = " open" if open_by_default else ""
    return (
        f'<details class="play-howto"{open_attr}>\n'
        "<summary>How to play</summary>\n"
        "<ol>\n"
        "<li>Tap one of your glowing units, then tap a highlighted square — "
        "that plans its order (a dot = move there; labeled pills = gather / "
        "deliver / hold).</li>\n"
        "<li>Repeat for each unit — a planned unit shows its order on the "
        "board; tap it again to change it.</li>\n"
        "<li>Press <strong>End turn</strong> to play your whole plan. The other "
        "side answers, and the feed above the board tells you what happened.</li>\n"
        "</ol>\n"
        "<ul>\n"
        f"<li><strong>Posts</strong> (ringed squares): {hold_note} to take one"
        f"{capture_who}; the counter on the post shows capture progress.</li>\n"
        "<li><strong>Resources</strong> (diamonds): <em>gather</em> while standing "
        "on one, then <em>deliver</em> on the mission square to bank them.</li>\n"
        "<li><strong>Flags</strong> mark mission squares — completing a mission "
        "scores a bonus.</li>\n"
        "</ul>\n"
        "</details>"
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
    "describe_events",
    "render_signed_out_hub_body",
    "render_hub_body",
    "render_board_lead",
    "render_events",
    "render_how_to",
    "render_notice",
    "render_plan_panel",
    "render_play_panel",
    "render_status_strip",
    "render_error_body",
]
