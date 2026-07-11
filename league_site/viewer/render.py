"""Transcript rendering for the match viewer: page model + HTML fragments.

Turns a domain :class:`~league_site.matches.match.Match` into a render-ready
:class:`MatchPageModel` (header + turn-by-turn transcript) via
:func:`build_page_model`, then into the ``<main>`` HTML fragment
:mod:`league_site.viewer.wsgi` embeds inside its page shell via
:func:`render_page_body`.

Escaping discipline
--------------------
Every plain-text fragment — participant display names, model/provider
labels, a turn's free-text ``"message"`` — is either ``html.escape``\\ d
directly or routed through :mod:`league_site.web._markdown`. That renderer
never emits an HTML tag it didn't generate itself: it recognizes a small,
fixed markdown subset (bold/italic/code/links/headings/...) and
``html.escape``\\ s everything else, so hostile input like
``<script>alert(1)</script>`` inside a message or a display name always
comes out as inert, escaped text — never a real tag — while genuine
markdown (``**bold**``, `` `code` ``) still renders. A turn's structured
"orders" payload (whatever is left of its action once a ``"message"`` key
is pulled out, or the whole action when it isn't a mapping) is never treated
as markdown: it is rendered as a compact, ``html.escape``\\ d JSON block, so
hostile content anywhere in an opaque action payload is equally inert.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from typing import Any

from league_site.matches.match import Match, MatchStatus
from league_site.matches.models import Participant, TurnRecord
from league_site.profiles.data import identity_slug
from league_site.ratings.ledger import RatingLedgerStore
from league_site.ratings.system import RatingIdentity
from league_site.web import _markdown

__all__ = [
    "ParticipantView",
    "TurnView",
    "MatchPageModel",
    "build_page_model",
    "render_page_body",
]

#: The action-dict key treated as free-text chat/commentary, rendered as
#: markdown rather than folded into the "orders" JSON block.
_MESSAGE_KEY = "message"


@dataclass(frozen=True)
class ParticipantView:
    """One participant, pre-escaped and ready to render."""

    participant_id: str
    display_name_html: str
    kind: str
    model_html: str | None
    provider_html: str | None
    score: float | None
    is_winner: bool
    rating: int | None
    profile_href: str | None


@dataclass(frozen=True)
class TurnView:
    """One turn, pre-escaped/rendered and ready to embed."""

    turn_number: int
    participant_id: str
    participant_name_html: str
    timestamp_iso: str
    message_html: str | None
    orders_html: str | None


@dataclass(frozen=True)
class MatchPageModel:
    """Everything :func:`render_page_body` needs to render one match page."""

    match_id: str
    game_id: str
    status: str
    is_live: bool
    participants: tuple[ParticipantView, ...]
    turns: tuple[TurnView, ...]
    winner_participant_id: str | None
    summary_html: str | None


def build_page_model(match: Match, ledger_store: RatingLedgerStore | None = None) -> MatchPageModel:
    """Build the render-ready page model for *match*.

    ``ledger_store``, if given, adds each participant's current rating (read
    fresh on every call — never cached, and never raises for an identity the
    ledger has no history for; see
    :meth:`~league_site.ratings.ledger.RatingLedgerStore.get`) to its
    :class:`ParticipantView`. Omitted (the default), ratings are left
    ``None`` and simply don't render.

    :attr:`ParticipantView.profile_href` is only populated once the match is
    :attr:`~league_site.matches.match.MatchStatus.COMPLETED` — an
    in-progress match's participants aren't linked out to their (still
    incomplete-for-this-match) profile pages yet; a finished match links
    every participant to ``/profiles/<slug>`` alongside the result.
    """
    is_completed = match.status is MatchStatus.COMPLETED
    scores = match.result.scores if match.result is not None else {}
    winner = match.result.winner_participant_id if match.result is not None else None

    participants = tuple(
        _build_participant_view(participant, scores, winner, ledger_store, link=is_completed)
        for participant in match.participants
    )
    turns = tuple(_build_turn_view(turn, match.participants) for turn in match.turns)

    summary_html = None
    if is_completed and match.result is not None and match.result.summary:
        summary_html = _markdown.render(match.result.summary)

    return MatchPageModel(
        match_id=match.match_id,
        game_id=match.game_id,
        status=match.status.value,
        is_live=not is_completed,
        participants=participants,
        turns=turns,
        winner_participant_id=winner,
        summary_html=summary_html,
    )


def _build_participant_view(
    participant: Participant,
    scores: dict[str, float],
    winner_participant_id: str | None,
    ledger_store: RatingLedgerStore | None,
    *,
    link: bool,
) -> ParticipantView:
    agent_identity = participant.agent_identity
    model_html = html.escape(agent_identity.model) if agent_identity is not None else None
    provider_html = html.escape(agent_identity.provider) if agent_identity is not None else None

    rating: int | None = None
    profile_href: str | None = None
    if ledger_store is not None or link:
        identity = RatingIdentity.from_participant(participant)
        if ledger_store is not None:
            rating = ledger_store.get(identity).rating
        if link:
            profile_href = f"/profiles/{identity_slug(identity)}"

    return ParticipantView(
        participant_id=participant.participant_id,
        display_name_html=html.escape(participant.display_name),
        kind=participant.kind.value,
        model_html=model_html,
        provider_html=provider_html,
        score=scores.get(participant.participant_id),
        is_winner=participant.participant_id == winner_participant_id,
        rating=rating,
        profile_href=profile_href,
    )


def _build_turn_view(turn: TurnRecord, participants: tuple[Participant, ...]) -> TurnView:
    name = next(
        (p.display_name for p in participants if p.participant_id == turn.participant_id),
        turn.participant_id,
    )
    message, orders_payload = _split_action(turn.action)
    return TurnView(
        turn_number=turn.turn_number,
        participant_id=turn.participant_id,
        participant_name_html=html.escape(name),
        timestamp_iso=turn.timestamp.isoformat(),
        message_html=_markdown.render(message) if message is not None else None,
        orders_html=_render_orders(orders_payload),
    )


def _split_action(action: Any) -> tuple[str | None, Any]:
    """Pull a free-text ``"message"`` out of *action*, if present.

    Returns ``(message, remainder)``: *remainder* is *action* with the
    ``"message"`` key removed (a dict action with nothing left over becomes
    ``None``, so an all-message action doesn't also render an empty orders
    block). *action* itself — including non-dict actions such as a bare
    string/number/list, which a game engine is free to use (see
    :class:`~league_site.matches.models.TurnRecord`'s docstring) — passes
    through unchanged as *remainder* when it isn't a mapping or doesn't
    carry a string ``"message"``.
    """
    if isinstance(action, dict) and isinstance(action.get(_MESSAGE_KEY), str):
        message = action[_MESSAGE_KEY]
        rest = {key: value for key, value in action.items() if key != _MESSAGE_KEY}
        return message, (rest or None)
    return None, action


def _render_orders(payload: Any) -> str | None:
    """Render *payload* (an opaque, JSON-safe action/orders value) as an escaped code block."""
    if payload is None:
        return None
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return f"<pre><code>{html.escape(text)}</code></pre>"


def render_page_body(model: MatchPageModel, *, board_html: str | None = None) -> str:
    """Render the ``<main>`` contents for one match-watch page (header + transcript).

    ``board_html``, if given, is a pre-rendered fragment (the shared match
    board — :mod:`league_site.viewer.board` — plus, on the play surface,
    its interaction hint/panel) slotted between the participants card and
    the result/transcript. Omitted (the default), the output is
    byte-identical to what this function always produced — non-grid matches
    and older callers render exactly as before.
    """
    parts = [
        f"<h1>Match <code>{html.escape(model.match_id)}</code></h1>",
        _render_status_line(model),
        _render_participants(model),
    ]
    if board_html:
        parts.append(board_html)
    if not model.is_live:
        result_html = _render_result(model)
        if result_html:
            parts.append(result_html)
    parts.append(_render_transcript(model))
    return "\n".join(parts)


def _render_status_line(model: MatchPageModel) -> str:
    game_id = html.escape(model.game_id)
    if model.is_live:
        indicator = (
            '<span class="live-indicator">&#9679; LIVE</span> '
            "— this page refreshes automatically as the match continues."
        )
    else:
        indicator = '<span class="live-indicator live-indicator-done">FINISHED</span>'
    return f"<p>Game: <code>{game_id}</code> &middot; {indicator}</p>"


def _render_participants(model: MatchPageModel) -> str:
    items = "".join(_render_participant_item(p) for p in model.participants)
    return f'<div class="card"><h2>Participants</h2><ul>{items}</ul></div>'


def _render_participant_item(participant: ParticipantView) -> str:
    chips = [f'<span class="chip">{participant.kind}</span>']
    if participant.model_html is not None:
        chips.append(f'<span class="chip">{participant.model_html}</span>')
    if participant.provider_html is not None:
        chips.append(f'<span class="chip">{participant.provider_html}</span>')
    if participant.rating is not None:
        chips.append(f'<span class="chip">rating {participant.rating}</span>')
    if participant.score is not None:
        chips.append(f'<span class="chip">score {participant.score:g}</span>')
    if participant.is_winner:
        chips.append('<span class="chip chip-winner">winner</span>')

    name_html = participant.display_name_html
    if participant.profile_href is not None:
        name_html = f'<a href="{html.escape(participant.profile_href)}">{name_html}</a>'

    return f"<li>{name_html} {''.join(chips)}</li>"


def _render_result(model: MatchPageModel) -> str:
    if model.winner_participant_id is not None:
        winner = next(
            (p for p in model.participants if p.participant_id == model.winner_participant_id),
            None,
        )
        winner_html = winner.display_name_html if winner is not None else "unknown"
        winner_line = f"<p><strong>Winner:</strong> {winner_html}</p>"
    else:
        winner_line = "<p>No winner recorded (draw or unscored).</p>"
    summary_line = model.summary_html or ""
    return f'<div class="card"><h2>Result</h2>{winner_line}{summary_line}</div>'


def _render_transcript(model: MatchPageModel) -> str:
    if not model.turns:
        return "<h2>Transcript</h2><p>No turns yet.</p>"
    entries = "".join(_render_turn(turn) for turn in model.turns)
    return f'<h2>Transcript</h2><ol class="transcript">{entries}</ol>'


def _render_turn(turn: TurnView) -> str:
    timestamp = html.escape(turn.timestamp_iso)
    header = (
        f"<p><strong>{turn.participant_name_html}</strong> "
        f"&middot; turn {turn.turn_number} &middot; "
        f'<time datetime="{timestamp}">{timestamp}</time>'
        "</p>"
    )
    body = (turn.message_html or "") + (turn.orders_html or "")
    return f'<li class="card turn-entry">{header}{body}</li>'
