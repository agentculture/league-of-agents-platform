"""Route-level tests for the play surface's visual board interaction.

Drives :func:`league_site.play.wsgi.with_play` against
:class:`tests._play_support.GridBoardEngine` (a ``GridLaneEngine``-shaped
engine with a real board, no subprocess) through the whole two-step flow the
board is the interface for: GET the play view (your actionable units are
selection links), GET ``?unit=<id>`` (its legal target cells become per-cell
POST controls), POST one control (exactly that action plays). The spectate
viewer renders the same board with none of the interaction layer.
"""

from __future__ import annotations

import html
import json
import re

from league_site.matches import InMemoryMatchStore, Match
from league_site.play.wsgi import with_play
from league_site.viewer.wsgi import viewer_app
from tests._play_support import (
    GridBoardEngine,
    call_page,
    human_participant,
    session_for,
)

_REGISTRY = {"solo-vs-bot": GridBoardEngine}


def _inner_app(environ, start_response):  # pragma: no cover - never reached
    start_response("200 OK", [("Content-Type", "text/plain; charset=utf-8")])
    return [b"inner"]


def _build() -> tuple[object, InMemoryMatchStore]:
    store = InMemoryMatchStore()
    app = with_play(_inner_app, match_store=store, engine_registry=_REGISTRY)
    return app, store


def _create_match(store: InMemoryMatchStore, participant) -> Match:
    match = Match.create(game_id="solo-vs-bot", participants=[participant])
    match.start(GridBoardEngine())
    store.save(match)
    return match


def _hidden_action_values(body: str) -> list[str]:
    """The (unescaped) submittable payloads carried by the board's cell controls."""
    return [html.unescape(value) for value in re.findall(r'name="action" value="([^"]*)"', body)]


_STYLE_RE = re.compile(r"<style>.*?</style>", re.S)


def _markup(body: str) -> str:
    """*body* without its inline ``<style>`` blocks — the stylesheet names the
    board classes too, so markup assertions must not match CSS text."""
    return _STYLE_RE.sub("", body)


# --- step 1: pick a unit ---------------------------------------------------------


def test_actionable_units_render_as_selection_links_on_the_board() -> None:
    app, store = _build()
    session = session_for(subject="42")
    match = _create_match(store, human_participant(session))
    play_path = f"/play/matches/{match.match_id}"

    status, _, body = call_page(app, "GET", play_path, session=session)
    assert status == "200 OK"
    markup = _markup(body)
    assert 'class="board"' in markup
    # both of the human's units have legal actions -> both are links
    assert f'href="{play_path}?unit=solo-u1"' in markup
    assert f'href="{play_path}?unit=solo-u2"' in markup
    # the opponent's unit is never selectable
    assert "?unit=house-u1" not in markup
    # no unit selected yet -> no per-cell submit controls on the board
    assert "board-target" not in markup
    # the select-based fallback stays available, collapsed as secondary
    assert "<details" in markup
    assert "<option" in markup


def test_board_renders_posts_resources_and_missions() -> None:
    app, store = _build()
    session = session_for(subject="42")
    match = _create_match(store, human_participant(session))

    _, _, body = call_page(app, "GET", f"/play/matches/{match.match_id}", session=session)
    markup = _markup(body)
    assert "board-post" in markup
    assert "board-res" in markup
    assert "board-mission" in markup


# --- step 2: pick a target --------------------------------------------------------


def test_selecting_a_unit_highlights_it_and_renders_its_targets_as_post_controls() -> None:
    app, store = _build()
    session = session_for(subject="42")
    match = _create_match(store, human_participant(session))
    play_path = f"/play/matches/{match.match_id}"

    status, _, body = call_page(app, "GET", f"{play_path}?unit=solo-u1", session=session)
    assert status == "200 OK"
    assert "board-unit-selected" in _markup(body)
    # a "change unit" way out: a link back to the un-selected view
    assert f'href="{play_path}"' in body
    # solo-u1 at (1,1): four in-bounds moves + hold = five controls
    values = [json.loads(value) for value in _hidden_action_values(body)]
    verbs = [entry["actions"][0]["action"] for entry in values]
    assert verbs.count("move") == 4
    assert verbs.count("hold") == 1
    assert all(entry["actions"][0]["unit_id"] == "solo-u1" for entry in values)
    # every control posts to the same turns endpoint the fallback form uses
    assert body.count(f'action="{play_path}/turns"') >= 5


def test_own_cell_verbs_render_as_stacked_labeled_buttons() -> None:
    app, store = _build()
    session = session_for(subject="42")
    match = _create_match(store, human_participant(session))

    # solo-u2 stands on a resource node: gather + hold share its own cell
    _, _, body = call_page(
        app, "GET", f"/play/matches/{match.match_id}?unit=solo-u2", session=session
    )
    assert "board-target-stack" in _markup(body)
    assert ">gather</button>" in body
    assert ">hold</button>" in body


def test_posting_a_board_control_plays_exactly_that_action() -> None:
    app, store = _build()
    session = session_for(subject="42")
    match = _create_match(store, human_participant(session))
    play_path = f"/play/matches/{match.match_id}"

    _, _, body = call_page(app, "GET", f"{play_path}?unit=solo-u1", session=session)
    move_value = next(
        value
        for value in _hidden_action_values(body)
        if json.loads(value)["actions"][0]["action"] == "move"
        and json.loads(value)["actions"][0]["to"] == [1, 2]
    )

    status, headers, _ = call_page(
        app, "POST", f"{play_path}/turns", form={"action": move_value}, session=session
    )
    assert status == "303 See Other"
    assert headers["Location"] == play_path

    reloaded = store.load(match.match_id)
    assert len(reloaded.turns) == 1
    assert reloaded.turns[0].action == json.loads(move_value)
    moved = next(u for u in reloaded.game_state["units"] if u["id"] == "solo-u1")
    assert moved["pos"] == [1, 2]


def test_out_of_date_board_submission_is_400_with_no_state_change() -> None:
    """A control captured before the board moved on still 4xxs safely."""
    app, store = _build()
    session = session_for(subject="42")
    match = _create_match(store, human_participant(session))
    play_path = f"/play/matches/{match.match_id}"

    _, _, body = call_page(app, "GET", f"{play_path}?unit=solo-u1", session=session)
    stale_value = next(
        value
        for value in _hidden_action_values(body)
        if json.loads(value)["actions"][0].get("to") == [1, 2]
    )
    # play it once (legal now)...
    status, _, _ = call_page(
        app, "POST", f"{play_path}/turns", form={"action": stale_value}, session=session
    )
    assert status == "303 See Other"
    # ...then replay the very same control: the unit has moved, so the old
    # target is no longer among the current legal actions.
    status, _, _ = call_page(
        app, "POST", f"{play_path}/turns", form={"action": stale_value}, session=session
    )
    assert status == "400 Bad Request"
    assert len(store.load(match.match_id).turns) == 1


def test_stale_unit_selection_degrades_to_the_unselected_board() -> None:
    app, store = _build()
    session = session_for(subject="42")
    match = _create_match(store, human_participant(session))

    status, _, body = call_page(
        app, "GET", f"/play/matches/{match.match_id}?unit=no-such-unit", session=session
    )
    assert status == "200 OK"
    markup = _markup(body)
    assert "board-unit-selected" not in markup
    assert "board-target" not in markup


# --- the spectate viewer stays non-interactive ------------------------------------


def test_spectate_watch_page_renders_the_board_with_no_forms_or_unit_links() -> None:
    _, store = _build()
    session = session_for(subject="42")
    match = _create_match(store, human_participant(session))

    viewer = viewer_app(store)
    status, _, body = call_page(
        viewer, "GET", f"/matches/{match.match_id}/watch", session_key_present=False
    )
    assert status == "200 OK"
    markup = _markup(body)
    assert 'class="board"' in markup  # spectators see the same board...
    assert "<form" not in markup  # ...but nothing on the page submits
    assert "?unit=" not in markup  # ...and no unit is a selection link
    assert "board-unit-live" not in markup
    assert "board-target" not in markup


def test_watch_page_of_a_non_grid_match_carries_no_board_markup() -> None:
    """Byte-level viewer stability for the stub family: no board, no residue."""
    from tests._viewer_support import start_match

    store = InMemoryMatchStore()
    match, _ = start_match()
    store.save(match)

    viewer = viewer_app(store)
    status, _, body = call_page(
        viewer, "GET", f"/matches/{match.match_id}/watch", session_key_present=False
    )
    assert status == "200 OK"
    markup = _markup(body)
    assert 'class="board"' not in markup
    assert "board-wrap" not in markup
