"""Route-level tests for the play surface's visual board interaction.

Drives :func:`league_site.play.wsgi.with_play` against
:class:`tests._play_support.GridBoardEngine` (a ``GridLaneEngine``-shaped
engine with a real board, no subprocess) through the whole plan-then-ack
flow the board is the interface for: GET the play view (your actionable
units are selection links), GET ``?unit=<id>`` (its legal target cells
become per-cell *staging links* — idempotent GETs), follow one (the order
joins the URL-carried plan), then POST the play panel's one End-turn form
(the whole plan plays as a single turn). The spectate viewer renders the
same board with none of the interaction layer.
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


def _staging_hrefs(body: str) -> list[str]:
    """The (unescaped) staging links on the board — every href carrying a
    ``staged=`` param."""
    return [html.unescape(href) for href in re.findall(r'href="([^"]*staged=[^"]*)"', body)]


def _staged_order(href: str) -> dict:
    """The (last) staged order a staging href plans."""
    from urllib.parse import parse_qs, urlparse

    values = parse_qs(urlparse(href).query)["staged"]
    return json.loads(values[-1])


def _plan_fields(body: str) -> list[str]:
    """The (unescaped) hidden ``order`` payloads in the End-turn form."""
    return [html.unescape(value) for value in re.findall(r'name="order" value="([^"]*)"', body)]


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
    assert f'href="{play_path}?unit=solo-u1#board"' in markup
    assert f'href="{play_path}?unit=solo-u2#board"' in markup
    # the opponent's unit is never selectable
    assert "unit=house-u1" not in markup
    # no unit selected yet -> no per-cell staging links on the board
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


# --- step 2: pick a target (stage the order) ---------------------------------------


def test_selecting_a_unit_highlights_it_and_renders_its_targets_as_staging_links() -> None:
    app, store = _build()
    session = session_for(subject="42")
    match = _create_match(store, human_participant(session))
    play_path = f"/play/matches/{match.match_id}"

    status, _, body = call_page(app, "GET", f"{play_path}?unit=solo-u1", session=session)
    assert status == "200 OK"
    markup = _markup(body)
    assert "board-unit-selected" in markup
    # a "pick a different unit" way out: a link back to the un-selected view
    assert f'href="{play_path}#board"' in body
    # solo-u1 at (1,1): four in-bounds moves + hold = five staging links
    orders = [_staged_order(href) for href in _staging_hrefs(markup)]
    verbs = [order["action"] for order in orders]
    assert verbs.count("move") == 4
    assert verbs.count("hold") == 1
    assert all(order["unit_id"] == "solo-u1" for order in orders)
    # staging is navigation, never submission: the board carries no form
    assert "<form" not in _markup(body).split('class="card play-panel"')[0]


def test_own_cell_verbs_render_as_stacked_labeled_pills() -> None:
    app, store = _build()
    session = session_for(subject="42")
    match = _create_match(store, human_participant(session))

    # solo-u2 stands on a resource node: gather + hold share its own cell
    _, _, body = call_page(
        app, "GET", f"/play/matches/{match.match_id}?unit=solo-u2", session=session
    )
    assert "board-target-stack" in _markup(body)
    assert ">gather</a>" in body
    assert ">hold</a>" in body


def test_staging_an_order_builds_the_plan_and_the_end_turn_form() -> None:
    app, store = _build()
    session = session_for(subject="42")
    match = _create_match(store, human_participant(session))
    play_path = f"/play/matches/{match.match_id}"

    _, _, body = call_page(app, "GET", f"{play_path}?unit=solo-u1", session=session)
    move_href = next(
        href for href in _staging_hrefs(_markup(body)) if _staged_order(href).get("to") == [1, 2]
    )
    # following the staging link is a plain GET: nothing has been submitted
    path_with_query = move_href.split("#", 1)[0]
    status, _, planned_body = call_page(app, "GET", path_with_query, session=session)
    assert status == "200 OK"
    assert len(store.load(match.match_id).turns) == 0

    markup = _markup(planned_body)
    # the planned unit wears the staged treatment; the other stays selectable
    assert "board-unit-staged" in markup
    assert "board-staged-mark" in markup
    # the End-turn form carries the plan + the turn it was made against
    (order_value,) = _plan_fields(planned_body)
    assert json.loads(order_value) == {"action": "move", "to": [1, 2], "unit_id": "solo-u1"}
    assert 'name="turn" value="0"' in planned_body
    assert "End turn" in planned_body


def test_ending_the_turn_plays_the_whole_plan_as_one_turn() -> None:
    app, store = _build()
    session = session_for(subject="42")
    match = _create_match(store, human_participant(session))
    play_path = f"/play/matches/{match.match_id}"

    move = json.dumps({"action": "move", "to": [1, 2], "unit_id": "solo-u1"}, sort_keys=True)
    gather = json.dumps({"action": "gather", "unit_id": "solo-u2"}, sort_keys=True)
    status, headers, _ = call_page(
        app,
        "POST",
        f"{play_path}/turns",
        form={"order": [move, gather], "turn": "0"},
        session=session,
    )
    assert status == "303 See Other"
    assert headers["Location"] == play_path

    reloaded = store.load(match.match_id)
    assert len(reloaded.turns) == 1
    assert reloaded.turns[0].action == {"actions": [json.loads(move), json.loads(gather)]}
    moved = next(u for u in reloaded.game_state["units"] if u["id"] == "solo-u1")
    gathered = next(u for u in reloaded.game_state["units"] if u["id"] == "solo-u2")
    assert moved["pos"] == [1, 2]
    assert gathered["carrying"] == 2


def test_a_double_submit_redirects_with_a_stale_notice_and_plays_nothing_twice() -> None:
    """2026-07-11 feedback: a double-press surfaced as a raw 400. The second
    submit's ``turn`` field no longer matches — it redirects to the fresh
    board with an honest notice instead."""
    app, store = _build()
    session = session_for(subject="42")
    match = _create_match(store, human_participant(session))
    play_path = f"/play/matches/{match.match_id}"

    move = json.dumps({"action": "move", "to": [1, 2], "unit_id": "solo-u1"}, sort_keys=True)
    form = {"order": [move], "turn": "0"}
    status, _, _ = call_page(app, "POST", f"{play_path}/turns", form=form, session=session)
    assert status == "303 See Other"

    status, headers, _ = call_page(app, "POST", f"{play_path}/turns", form=form, session=session)
    assert status == "303 See Other"
    assert headers["Location"] == f"{play_path}?notice=stale"
    assert len(store.load(match.match_id).turns) == 1

    # and the notice renders on the landing page
    _, _, body = call_page(app, "GET", f"{play_path}?notice=stale", session=session)
    assert "play-notice" in body


def test_an_illegal_plan_redirects_with_a_notice_and_no_state_change() -> None:
    app, store = _build()
    session = session_for(subject="42")
    match = _create_match(store, human_participant(session))
    play_path = f"/play/matches/{match.match_id}"

    bogus = json.dumps({"action": "move", "to": [9, 9], "unit_id": "solo-u1"}, sort_keys=True)
    status, headers, _ = call_page(
        app,
        "POST",
        f"{play_path}/turns",
        form={"order": [bogus], "turn": "0"},
        session=session,
    )
    assert status == "303 See Other"
    assert headers["Location"] == f"{play_path}?notice=illegal"
    assert len(store.load(match.match_id).turns) == 0


def test_two_orders_for_the_same_unit_are_refused() -> None:
    app, store = _build()
    session = session_for(subject="42")
    match = _create_match(store, human_participant(session))
    play_path = f"/play/matches/{match.match_id}"

    move = json.dumps({"action": "move", "to": [1, 2], "unit_id": "solo-u1"}, sort_keys=True)
    hold = json.dumps({"action": "hold", "unit_id": "solo-u1"}, sort_keys=True)
    status, headers, _ = call_page(
        app,
        "POST",
        f"{play_path}/turns",
        form={"order": [move, hold], "turn": "0"},
        session=session,
    )
    assert status == "303 See Other"
    assert headers["Location"] == f"{play_path}?notice=illegal"
    assert len(store.load(match.match_id).turns) == 0


def test_stale_staged_orders_in_the_url_are_silently_dropped() -> None:
    """A bookmarked/back-button plan whose orders are no longer legal renders
    the fresh board rather than erroring — same degrade as a stale
    ``?unit=`` selection."""
    app, store = _build()
    session = session_for(subject="42")
    match = _create_match(store, human_participant(session))
    play_path = f"/play/matches/{match.match_id}"

    bogus = json.dumps({"action": "move", "to": [9, 9], "unit_id": "solo-u1"}, sort_keys=True)
    from urllib.parse import quote

    status, _, body = call_page(app, "GET", f"{play_path}?staged={quote(bogus)}", session=session)
    assert status == "200 OK"
    markup = _markup(body)
    assert "board-unit-staged" not in markup
    assert _plan_fields(body) == []


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


def test_last_turn_events_and_rejections_render_as_the_feed() -> None:
    app, store = _build()
    session = session_for(subject="42")
    match = _create_match(store, human_participant(session))
    match.game_state["last_turn_events"] = [
        {"kind": "post_captured", "post_id": "cp-mid", "team": "house"},
        {"kind": "gathered", "unit_id": "solo-u2", "team": "solo", "amount": 1},
    ]
    match.game_state["last_turn_rejections"] = [
        {"team_id": "solo", "unit_id": "solo-u1", "reason": "target beyond this role's move range"}
    ]
    match.game_state["team_participants"] = {
        "solo": [human_participant(session).participant_id],
        "house": [],
    }
    store.save(match)

    _, _, body = call_page(app, "GET", f"/play/matches/{match.match_id}", session=session)
    assert "Last turn" in body
    assert "House bot captured cp-mid." in body
    assert "solo-u2 (You) gathered 1." in body
    assert "Refused — solo-u1: target beyond this role&#x27;s move range" in body


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
    assert "unit=" not in markup  # ...and no unit is a selection link
    assert "board-unit-live" not in markup
    assert "board-target" not in markup
    assert "staged=" not in markup


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
